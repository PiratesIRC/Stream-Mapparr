"""Rollout safety: old and new workers run side by side for one release.

storage.ROLLOUT_DUAL_PATHS is True in this release. While it is, a worker on the new code
also honours the old /data state that workers on the previous release still write: the
slot claim, the M3U refresh lock and pending flag, the operation lock and the tally tail.

Non-legacy tests rebind the session PluginConfig to a temp data dir through the rollout
fixture (monkeypatch restores every constant). Tests that need real flock skip on Windows.
The flag and this file's dual-path tests are removed in the release after next.
"""
import io
import json
import os
import types
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

DAY = "2026-11-23"
SLOT = "05:05"
KEY = "stream-mapparr"


class Log:
    def __init__(self):
        self.infos, self.debugs, self.warnings, self.errors = [], [], [], []

    def info(self, *a):
        self.infos.append(a)

    def debug(self, *a):
        self.debugs.append(a)

    def warning(self, *a):
        self.warnings.append(a)

    def error(self, *a):
        self.errors.append(a)


@pytest.fixture
def rollout(plugin_module, tmp_path, monkeypatch):
    """Old /data paths under tmp/old, new data dirs under tmp/plugins/_data/<slug>."""
    cfg = plugin_module.PluginConfig
    root = tmp_path / "plugins"
    old = tmp_path / "old"
    old.mkdir()
    monkeypatch.setattr(cfg, "LEGACY_PATHS", {
        **cfg.LEGACY_PATHS,
        "SCHEDULER_LOCK_FILE": str(old / "stream_mapparr_scheduler.lock"),
        "SCHEDULER_LAST_RUN_FILE": str(old / "stream_mapparr_scheduler_last_run.json"),
        "M3U_REFRESH_LOCK_FILE": str(old / "stream_mapparr_m3u_refresh.lock"),
        "M3U_REFRESH_PENDING_FILE": str(old / "stream_mapparr_m3u_refresh_pending"),
        "OPERATION_LOCK_FILE": str(old / "stream_mapparr_operation.lock"),
        "MATCH_TALLY_FILE": str(old / "stream_mapparr_match_counts.jsonl"),
    })

    def bind_to(slug):
        data = root / "_data" / slug
        data.mkdir(parents=True, exist_ok=True)
        layout = plugin_module.storage.Layout(False, str(data), str(root), KEY, "test")
        monkeypatch.setattr(cfg, "STORAGE_LAYOUT", layout)
        monkeypatch.setattr(cfg, "SCHEDULER_LOCK_FILE", layout.beside(".scheduler.lock"))
        monkeypatch.setattr(cfg, "SCHEDULER_LAST_RUN_FILE", layout.beside(".scheduler_last_run.json"))
        monkeypatch.setattr(cfg, "M3U_REFRESH_LOCK_FILE", layout.beside(".m3u_refresh.lock"))
        monkeypatch.setattr(cfg, "M3U_REFRESH_PENDING_FILE", layout.beside(".m3u_refresh_pending"))
        monkeypatch.setattr(cfg, "OPERATION_LOCK_FILE", layout.beside(".operation.lock"))
        monkeypatch.setattr(cfg, "MATCH_TALLY_FILE", str(data / "match_counts.jsonl"))
        return layout

    bind_to("foo")
    return SimpleNamespace(bind_to=bind_to, cfg=cfg, old=old, root=root, tmp=tmp_path,
                           plugin=plugin_module, storage=plugin_module.storage)


def _plugin(r):
    return r.plugin.Plugin.__new__(r.plugin.Plugin)


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _old_record(r):
    return r.cfg.LEGACY_PATHS["SCHEDULER_LAST_RUN_FILE"]


def test_claim_blocked_by_old_record(rollout):
    Path(_old_record(rollout)).write_text(json.dumps({SLOT: DAY}), encoding="utf-8")
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, Log()) is False


def test_claim_writes_both_records(rollout):
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, Log()) is True
    assert _read(rollout.cfg.SCHEDULER_LAST_RUN_FILE) == {SLOT: DAY}
    assert _read(_old_record(rollout)) == {SLOT: DAY}


def test_claim_skips_slot_when_a_write_fails(rollout, monkeypatch):
    old_path = _old_record(rollout)
    real_write = rollout.storage.atomic_write_json

    def failing_for_old(path, obj):
        if path == old_path:
            raise OSError("disk full")
        return real_write(path, obj)

    monkeypatch.setattr(rollout.storage, "atomic_write_json", failing_for_old)
    log = Log()
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, log) is False
    assert len(log.errors) == 1
    assert not os.path.exists(old_path)


def test_claim_skips_slot_when_lock_cannot_open(rollout):
    os.makedirs(rollout.cfg.SCHEDULER_LOCK_FILE)   # a directory where the lock file belongs
    log = Log()
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, log) is False
    assert len(log.errors) == 1
    assert not os.path.exists(_old_record(rollout))


def test_slug_interleaving_runs_once(rollout):
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, Log()) is True   # worker on slug foo
    rollout.bind_to("bar")   # worker on slug bar; the beside files are slug-independent
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, Log()) is False


def test_fresh_hub_install_claims_once(rollout):
    p, log = _plugin(rollout), Log()
    assert p._claim_scheduled_slot(SLOT, DAY, log) is True
    assert p._claim_scheduled_slot(SLOT, DAY, log) is False
    assert log.errors == []


def test_legacy_mode_claim_uses_data_paths_only(rollout):
    data = rollout.tmp / "legacy_data"
    data.mkdir()
    cfg = rollout.cfg
    layout = rollout.bind_to("foo")
    cfg.STORAGE_LAYOUT = rollout.storage.Layout(True, None, None, KEY, "test")
    cfg.SCHEDULER_LOCK_FILE = str(data / "stream_mapparr_scheduler.lock")
    cfg.SCHEDULER_LAST_RUN_FILE = str(data / "stream_mapparr_scheduler_last_run.json")
    p, log = _plugin(rollout), Log()
    assert p._claim_scheduled_slot(SLOT, DAY, log) is True
    assert p._claim_scheduled_slot(SLOT, DAY, log) is False
    assert (data / "stream_mapparr_scheduler_last_run.json").exists()
    assert not os.path.exists(layout.beside(".scheduler_last_run.json"))
    assert not os.path.exists(layout.beside(".scheduler.lock"))


def test_m3u_pending_flag_either_file(rollout):
    p, log = _plugin(rollout), Log()
    new = rollout.cfg.M3U_REFRESH_PENDING_FILE
    old = rollout.cfg.LEGACY_PATHS["M3U_REFRESH_PENDING_FILE"]
    p._set_m3u_refresh_pending(log)
    assert os.path.exists(new) and os.path.exists(old)
    p._clear_m3u_refresh_pending(log)
    assert not os.path.exists(new) and not os.path.exists(old)
    open(old, "w").close()   # a flag left by an old worker alone still counts
    assert p._m3u_refresh_pending_set(log) is True
    p._clear_m3u_refresh_pending(log)
    assert p._m3u_refresh_pending_set(log) is False
    assert not os.path.exists(old)


def test_m3u_lock_held_in_old_path_blocks(rollout):
    fcntl = pytest.importorskip("fcntl")
    p, log = _plugin(rollout), Log()
    old = rollout.cfg.LEGACY_PATHS["M3U_REFRESH_LOCK_FILE"]
    new = rollout.cfg.M3U_REFRESH_LOCK_FILE

    holder = open(old, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert p._acquire_m3u_refresh_flock(log) is None
    probe = open(new, "a")   # the failed acquire must not leave the new lock held
    fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    probe.close()
    holder.close()

    holder = open(new, "a")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert p._acquire_m3u_refresh_flock(log) is None
    holder.close()

    fd = p._acquire_m3u_refresh_flock(log)
    assert fd is not None and not isinstance(fd, io.IOBase)   # dual mode returns the holder
    p._release_m3u_refresh_flock(fd, log)
    for path in (old, new):
        check = open(path, "a")
        fcntl.flock(check.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)   # released, not leaked
        check.close()


def test_operation_lock_either_file(rollout, monkeypatch):
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)   # no pid is probed in this test
    p, log = _plugin(rollout), Log()
    new = rollout.cfg.OPERATION_LOCK_FILE
    old = rollout.cfg.LEGACY_PATHS["OPERATION_LOCK_FILE"]
    with open(old, "w", encoding="utf-8") as fh:
        json.dump({"action": "sort_streams", "start_time": datetime.now().isoformat()}, fh)
    is_locked, info = p._check_operation_lock(log)
    assert is_locked is True and info["action"] == "sort_streams"
    assert p._acquire_operation_lock("add_streams_to_channels", log) is False

    os.remove(old)
    with open(new, "w", encoding="utf-8") as fh:
        json.dump({"action": "sort_streams", "start_time": datetime.now().isoformat()}, fh)
    assert p._check_operation_lock(log)[0] is True
    p._release_operation_lock(log)
    assert not os.path.exists(new) and not os.path.exists(old)

    assert p._acquire_operation_lock("sort_streams", log) is True
    assert os.path.exists(new) and os.path.exists(old)
    p._release_operation_lock(log)
    assert not os.path.exists(new) and not os.path.exists(old)


def test_clear_operation_lock_action_clears_both(rollout):
    p, log = _plugin(rollout), Log()
    stamp = json.dumps({"action": "sort_streams", "start_time": datetime.now().isoformat()})
    for path in (rollout.cfg.OPERATION_LOCK_FILE, rollout.cfg.LEGACY_PATHS["OPERATION_LOCK_FILE"]):
        Path(path).write_text(stamp, encoding="utf-8")
    result = p.clear_operation_lock_action({}, log)
    assert result["status"] == "success"
    assert not os.path.exists(rollout.cfg.OPERATION_LOCK_FILE)
    assert not os.path.exists(rollout.cfg.LEGACY_PATHS["OPERATION_LOCK_FILE"])


def test_tally_append_runs_tail_first(rollout):
    p = _plugin(rollout)
    p._report_usage = lambda *a, **k: None
    layout = rollout.cfg.STORAGE_LAYOUT
    old_tally = rollout.cfg.LEGACY_PATHS["MATCH_TALLY_FILE"]
    new_tally = rollout.cfg.MATCH_TALLY_FILE
    copied = '{"action": "a", "channels": 1, "streams": 1, "ts": 1.0}\n'
    old_worker = '{"action": "a", "channels": 1, "streams": 2, "ts": 2.0}\n'
    # Bytes, not text: write_text turns "\n" into "\r\n" on Windows and skews the offset.
    Path(old_tally).write_bytes((copied + old_worker).encode("ascii"))   # old worker kept appending
    Path(new_tally).write_bytes(copied.encode("ascii"))                  # the carry-over copy
    Path(layout.data_dir, "match_counts.offsets.json").write_text(
        json.dumps({old_tally: len(copied)}), encoding="utf-8")

    p._record_streams_matched("a", 1, 3, False)

    lines = Path(new_tally).read_bytes().decode("ascii").splitlines()
    assert [json.loads(x)["streams"] for x in lines] == [1, 2, 3]


def test_today_slot_not_rerun_after_record_copy(rollout):
    (rollout.old / "stream_mapparr_scheduler_last_run.json").write_text(
        json.dumps({SLOT: DAY}), encoding="utf-8")
    layout = rollout.cfg.STORAGE_LAYOUT
    items = rollout.storage.items_for(rollout.cfg, layout)
    rollout.storage.migrate(layout, items, Log(), version="test")
    assert os.path.exists(layout.beside(".scheduler_last_run.json"))
    assert _plugin(rollout)._claim_scheduled_slot(SLOT, DAY, Log()) is False


def test_rollout_flag_off_uses_only_new_paths(rollout, monkeypatch):
    monkeypatch.setattr(rollout.storage, "ROLLOUT_DUAL_PATHS", False)
    old = _old_record(rollout)
    Path(old).write_text(json.dumps({"16:00": DAY}), encoding="utf-8")
    p = _plugin(rollout)
    assert p._rollout_old_path("SCHEDULER_LOCK_FILE") is None
    assert p._claim_scheduled_slot(SLOT, DAY, Log()) is True
    assert _read(rollout.cfg.SCHEDULER_LAST_RUN_FILE) == {SLOT: DAY}
    assert _read(old) == {"16:00": DAY}


def test_rollout_dual_paths_is_temporary(plugin_module):
    storage = plugin_module.storage
    assert storage.ROLLOUT_DUAL_PATHS is True
    lines = Path(storage.__file__).read_text(encoding="utf-8").splitlines()
    flag_line = next(i for i, ln in enumerate(lines) if ln.startswith("ROLLOUT_DUAL_PATHS = True"))
    block = "\n".join(lines[max(0, flag_line - 12):flag_line])
    assert "remove in the release after next" in block


def test_legacy_mode_takes_exactly_one_flock(plugin_module, tmp_path, monkeypatch):
    assert plugin_module.PluginConfig.STORAGE_LAYOUT.legacy is True
    calls = []
    fake = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8,
                                 flock=lambda fileno, op: calls.append(op))
    monkeypatch.setattr(plugin_module, "fcntl", fake)
    monkeypatch.setattr(plugin_module.PluginConfig, "M3U_REFRESH_LOCK_FILE",
                        str(tmp_path / "m3u_refresh.lock"))
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    log = Log()
    fd = p._acquire_m3u_refresh_flock(log)
    assert isinstance(fd, io.IOBase)   # legacy returns the plain file object
    assert len(calls) == 1
    p._release_m3u_refresh_flock(fd, log)
    assert len(calls) == 2


def test_operation_lock_and_pending_flag_shared_across_slugs(rollout, monkeypatch):
    """Workers on an old slug and a new slug see one operation lock and one pending flag."""
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)   # no pid is probed in this test
    p, log = _plugin(rollout), Log()
    foo = rollout.cfg.OPERATION_LOCK_FILE
    foo_pending = rollout.cfg.M3U_REFRESH_PENDING_FILE
    foo_m3u = rollout.cfg.M3U_REFRESH_LOCK_FILE
    assert p._acquire_operation_lock("sort_streams", log) is True   # worker on slug foo
    p._set_m3u_refresh_pending(log)
    rollout.bind_to("bar")   # worker on slug bar, same key
    assert rollout.cfg.OPERATION_LOCK_FILE == foo
    assert rollout.cfg.M3U_REFRESH_PENDING_FILE == foo_pending
    assert rollout.cfg.M3U_REFRESH_LOCK_FILE == foo_m3u
    assert p._check_operation_lock(log)[0] is True
    assert p._m3u_refresh_pending_set(log) is True


def test_the_state_locks_are_beside_the_data_dirs(rollout):
    layout = rollout.cfg.STORAGE_LAYOUT
    items = {item.name: item for item in rollout.storage.items_for(rollout.cfg, layout)}
    for name, leaf in (("OPERATION_LOCK_FILE", ".operation.lock"),
                       ("M3U_REFRESH_LOCK_FILE", ".m3u_refresh.lock"),
                       ("M3U_REFRESH_PENDING_FILE", ".m3u_refresh_pending")):
        assert items[name].new == layout.beside(leaf)
        assert items[name].kind == "nocopy"


def test_second_pass_leaves_the_carry_over_record_alone(rollout):
    """A process after the first pass copies nothing and must not rewrite the record."""
    cfg = rollout.cfg
    layout = cfg.STORAGE_LAYOUT
    Path(cfg.LEGACY_PATHS["SETTINGS_FILE"]).write_text('{"x": 1}', encoding="utf-8")
    items = rollout.storage.items_for(cfg, layout)
    first = Log()
    rollout.storage.migrate(layout, items, first, version="t")
    record = Path(layout.data_dir, "migrated.json")
    before = record.read_bytes()
    assert any("carry-over copied" in str(a) for a in first.infos)

    rollout.storage._state().migrated_dirs.clear()   # a new process: flag not set
    second = Log()
    rollout.storage.migrate(layout, items, second, version="t")
    assert record.read_bytes() == before
    assert not any("carry-over copied" in str(a) for a in second.infos)
