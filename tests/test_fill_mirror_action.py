"""Fill Mirror Backups as an action: what it writes, and when it writes nothing.

The planning rules are pinned in test_mirror_backups.py. These tests drive
fill_mirror_backups_action against small in-memory stand-ins for the three
tables it reads (M3UAccount, ChannelStream, Stream) so the write path, the
snapshot guard and the scheduled-run wiring are exercised without Django.
"""
import json
import logging
import os
import sys
import types

import pytest

log = logging.getLogger("t")

ACCOUNT_ROWS = [
    {"id": 1, "name": "custom", "server_group_id": None, "is_active": True, "server_url": None},
    {"id": 6, "name": "acct6", "server_group_id": 1, "is_active": True, "server_url": "http://a.example:80/x"},
    {"id": 7, "name": "acct7", "server_group_id": 1, "is_active": True, "server_url": "http://b.example:80/x"},
    {"id": 8, "name": "acct8", "server_group_id": 1, "is_active": True, "server_url": "http://a.example:80/y"},
    {"id": 9, "name": "acct9", "server_group_id": 1, "is_active": False, "server_url": "http://c.example:80/x"},
]

# stream pk -> (account id, provider stream id, name)
STREAMS = {
    10: (6, 555, "CNN HD"), 20: (7, 555, "CNN HD"), 30: (8, 555, "CNN HD"), 40: (9, 555, "CNN HD"),
    11: (6, 777, "ESPN"), 21: (7, 777, "ESPN"),
    99: (1, None, "Could Not Dispatch"),
}


class _QS:
    def __init__(self, model, kw=None):
        self.model, self.kw = model, kw or {}

    def filter(self, **kw):
        return _QS(self.model, {**self.kw, **kw})

    def order_by(self, *a):
        return self

    def select_for_update(self):
        return self

    def all(self):
        return self

    def values(self, *fields):
        return [{f: r[f] for f in fields} for r in self.model.rows()]

    def values_list(self, *fields, flat=False):
        rows = self.model.select(self.kw, fields)
        return [r[0] for r in rows] if flat else rows

    def delete(self):
        self.model.delete(self.kw)

    def bulk_create(self, objs):
        self.model.bulk_create(objs)


class _Manager(_QS):
    pass


class FakeAccount:
    @staticmethod
    def rows():
        return ACCOUNT_ROWS


FakeAccount.objects = _Manager(FakeAccount)


class FakeStream:
    @staticmethod
    def select(kw, fields):
        out = []
        for pk in sorted(STREAMS):
            acct, pid, name = STREAMS[pk]
            if "id__in" in kw and pk not in kw["id__in"]:
                continue
            if "m3u_account_id__in" in kw and acct not in kw["m3u_account_id__in"]:
                continue
            if "stream_id__in" in kw and pid not in kw["stream_id__in"]:
                continue
            row = {"id": pk, "name": name, "m3u_account_id": acct, "stream_id": pid}
            out.append(tuple(row[f] for f in fields))
        return out


FakeStream.objects = _Manager(FakeStream)


class FakeChannelStream:
    store = {}          # channel id -> [stream pk] in order
    changed_under = {}  # channel id -> order to swap in on the locked re-read

    def __init__(self, channel_id, stream_id, order):
        self.channel_id, self.stream_id, self.order = channel_id, stream_id, order

    @classmethod
    def select(cls, kw, fields):
        if "channel_id" in kw:
            cid = kw["channel_id"]
            if cid in cls.changed_under:
                cls.store[cid] = cls.changed_under.pop(cid)
            return [(sid,) for sid in cls.store.get(cid, [])]
        out = []
        for cid in sorted(kw.get("channel_id__in", cls.store)):
            for sid in cls.store.get(cid, []):
                acct, pid, _ = STREAMS[sid]
                row = {"channel_id": cid, "stream_id": sid,
                       "stream__m3u_account_id": acct, "stream__stream_id": pid}
                out.append(tuple(row[f] for f in fields))
        return out

    @classmethod
    def delete(cls, kw):
        cls.store[kw["channel_id"]] = []

    @classmethod
    def bulk_create(cls, objs):
        for o in sorted(objs, key=lambda o: o.order):
            cls.store.setdefault(o.channel_id, []).append(o.stream_id)


FakeChannelStream.objects = _Manager(FakeChannelStream)


class _Atomic:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def plugin(plugin_module, monkeypatch, tmp_path):
    FakeChannelStream.store = {
        100: [10, 99],          # host A only: gains acct7's mirror
        200: [11, 21, 99],      # hosts A and B: nothing to add
        300: [99],              # placeholder only: nothing to mirror
    }
    FakeChannelStream.changed_under = {}
    monkeypatch.setattr(plugin_module, "ChannelStream", FakeChannelStream)
    monkeypatch.setattr(plugin_module, "Stream", FakeStream)
    m3u_models = types.ModuleType("apps.m3u.models")
    m3u_models.M3UAccount = FakeAccount
    monkeypatch.setitem(sys.modules, "apps.m3u.models", m3u_models)
    monkeypatch.setattr(sys.modules["django.db"], "transaction",
                        types.SimpleNamespace(atomic=lambda: _Atomic()), raising=False)

    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.version = "test"
    p.BUG_REPORT_DIR = str(tmp_path / "config")
    channels = [{"id": 100, "name": "CNN", "channel_number": 1},
                {"id": 200, "name": "ESPN", "channel_number": 2},
                {"id": 300, "name": "KFDA", "channel_number": 3}]
    monkeypatch.setattr(p, "_channels_in_scope", lambda settings, logger: (channels, None))
    monkeypatch.setattr(p, "_trigger_frontend_refresh", lambda settings, logger: True)
    return p


def test_a_live_run_adds_the_other_hosts_mirror_before_the_placeholder(plugin, plugin_module):
    result = plugin.fill_mirror_backups_action({}, log)
    assert result["status"] == "success", result
    assert FakeChannelStream.store[100] == [10, 20, 99]
    assert FakeChannelStream.store[200] == [11, 21, 99]
    assert FakeChannelStream.store[300] == [99]
    assert "Added 1 mirror stream(s) to 1 channel(s)" in result["message"]


def test_a_live_run_saves_the_previous_order_first(plugin, plugin_module):
    plugin.fill_mirror_backups_action({}, log)
    snap_dir = plugin_module.PluginConfig.CHANNEL_SNAPSHOT_DIR
    files = [f for f in os.listdir(snap_dir) if f.startswith("stream_mapparr_mirror_fill_")]
    assert len(files) == 1
    with open(os.path.join(snap_dir, files[0])) as fh:
        snap = json.load(fh)
    assert snap["channels"] == {"100": [10, 99]}


def test_dry_run_changes_nothing_and_saves_no_snapshot(plugin, plugin_module):
    result = plugin.fill_mirror_backups_action({"dry_run_mode": True}, log)
    assert "would add 1 mirror stream(s) to 1 channel(s)" in result["message"]
    assert FakeChannelStream.store[100] == [10, 99]
    assert not os.path.isdir(plugin_module.PluginConfig.CHANNEL_SNAPSHOT_DIR) or \
        os.listdir(plugin_module.PluginConfig.CHANNEL_SNAPSHOT_DIR) == []


def test_a_failed_snapshot_writes_nothing(plugin, monkeypatch):
    """_write_json_atomic swallows its own errors, so the guard must look at the disk."""
    monkeypatch.setattr(plugin, "_write_json_atomic", lambda path, data: None)
    result = plugin.fill_mirror_backups_action({}, log)
    assert result["status"] == "error"
    assert "nothing was changed" in result["message"]
    assert FakeChannelStream.store[100] == [10, 99]


def test_a_channel_changed_during_the_run_is_left_alone(plugin):
    FakeChannelStream.changed_under = {100: [10, 30, 99]}
    result = plugin.fill_mirror_backups_action({}, log)
    assert FakeChannelStream.store[100] == [10, 30, 99]
    assert "1 channel(s) changed while this ran" in result["message"]


def test_the_readout_names_accounts_and_never_a_provider_host(plugin):
    result = plugin.fill_mirror_backups_action({}, log)
    with open(result["file"], encoding="utf-8") as fh:
        text = fh.read()
    assert "+ acct7: CNN HD" in text
    assert "acct9" in text and "INACTIVE" in text
    for host in ("a.example", "b.example", "c.example"):
        assert host not in text
    assert "a.example" not in result["message"]


def test_no_server_group_means_nothing_to_do(plugin, monkeypatch):
    rows = [dict(r, server_group_id=None) for r in ACCOUNT_ROWS]
    monkeypatch.setattr(FakeAccount, "rows", staticmethod(lambda: rows))
    result = plugin.fill_mirror_backups_action({}, log)
    assert result["status"] == "success"
    assert "No M3U account is in a server group" in result["message"]
    assert FakeChannelStream.store[100] == [10, 99]


# --------------------------------------------------------------------------- #
# Scheduled run wiring
# --------------------------------------------------------------------------- #
def _wire(monkeypatch, p):
    calls = []
    monkeypatch.setattr(p, "_wait_for_iptv_checker_completion", lambda s, l: True)
    monkeypatch.setattr(p, "load_process_channels_action",
                        lambda s, l, context=None: {"status": "success", "message": "m"})
    for name, tag in (("fill_mirror_backups_action", "mirror"), ("sort_streams_action", "sort"),
                      ("add_streams_to_channels_action", "match")):
        monkeypatch.setattr(p, name, lambda s, l, context=None, is_scheduled=False, trigger=None, _t=tag:
                            calls.append((_t, is_scheduled)) or {"status": "success"})
    monkeypatch.setattr(p, "_notify_bridge", lambda: types.SimpleNamespace(
        SCHEDULED_RUN_FILE="/x", write_scheduled_run_ts=lambda path, ts: None))
    monkeypatch.setattr(p, "_report_usage", lambda s, l: None)
    return calls


def test_the_scheduled_run_fills_mirrors_before_it_sorts(plugin, monkeypatch):
    calls = _wire(monkeypatch, plugin)
    plugin._run_scheduled_sequence({"scheduled_fill_mirror_backups": True, "scheduled_sort_streams": True,
                                    "scheduled_match_streams": False}, log)
    assert calls == [("mirror", True), ("sort", True)]


def test_the_scheduled_mirror_step_is_off_by_default(plugin, monkeypatch):
    calls = _wire(monkeypatch, plugin)
    plugin._run_scheduled_sequence({"scheduled_sort_streams": True, "scheduled_match_streams": False}, log)
    assert calls == [("sort", True)]


def test_the_schedule_setting_sits_in_the_scheduling_section(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    ids = [f["id"] for f in inst.fields]
    section = ids.index("_section_scheduling")
    nxt = min((i for i, fid in enumerate(ids) if fid.startswith("_section_") and i > section), default=len(ids))
    assert section < ids.index("scheduled_fill_mirror_backups") < nxt


def test_nothing_to_add_reports_why_each_channel_gained_nothing(plugin, monkeypatch):
    """A placeholder-only channel is not 'already covered'; the message says which is which."""
    FakeChannelStream.store[100] = [10, 20, 99]
    channels = [{"id": 100, "name": "CNN", "channel_number": 1},
                {"id": 200, "name": "ESPN", "channel_number": 2},
                {"id": 300, "name": "KFDA", "channel_number": 3},
                {"id": 400, "name": "Empty", "channel_number": 4}]
    monkeypatch.setattr(plugin, "_channels_in_scope", lambda settings, logger: (channels, None))
    result = plugin.fill_mirror_backups_action({"dry_run_mode": True}, log)
    msg = result["message"]
    assert msg.startswith("Nothing to add.")
    assert "Of 4 channels in scope: 2 already have every active host" in msg
    assert "2 have no server-group stream to copy from" in msg
    assert "0 miss a host" in msg
    assert len(msg) <= 280
    with open(result["file"], encoding="utf-8") as fh:
        text = fh.read()
    assert "No server-group stream to copy: 2" in text
