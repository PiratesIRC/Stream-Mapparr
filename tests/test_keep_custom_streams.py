"""Issue #70: Match and Assign with Overwrite on deleted the Could Not Dispatch
fallback stream.

Could Not Dispatch attaches one custom stream (Stream.is_custom, belonging to
Dispatcharr's built-in custom account) as the LAST stream of every channel, so
Dispatcharr's failover reaches it only after every provider stream failed.
Overwrite Existing Streams deleted every ChannelStream row on the channel and
wrote back only the matched provider streams, so the fallback was gone after
every run. Sort read the fallback with the provider streams and could rank it
above them by its stats.

With keep_custom_streams on (the default), custom streams already on a channel
are written back after the streams Stream-Mapparr chose, in their old order.
"""
from unittest.mock import MagicMock

import pytest


class _FakeQuerySet:
    def __init__(self, store, rows):
        self._store = store
        self._rows = rows

    def order_by(self, field):
        return _FakeQuerySet(self._store, sorted(self._rows, key=lambda r: r[field]))

    def values_list(self, field, flat=False):
        return [r[field] for r in self._rows]

    def delete(self):
        ids = {id(r) for r in self._rows}
        self._store.rows[:] = [r for r in self._store.rows if id(r) not in ids]


class _FakeManager:
    def __init__(self, store):
        self._store = store

    def filter(self, channel_id=None, stream__is_custom=None):
        rows = [r for r in self._store.rows if r["channel_id"] == channel_id]
        if stream__is_custom is not None:
            rows = [r for r in rows
                    if (r["stream_id"] in self._store.custom_ids) == stream__is_custom]
        return _FakeQuerySet(self._store, rows)

    def bulk_create(self, objs):
        for o in objs:
            self._store.rows.append(dict(o.kwargs))


class _FakeStore:
    def __init__(self, custom_ids=()):
        self.rows = []
        self.custom_ids = set(custom_ids)

    def put(self, channel_id, stream_ids):
        for order, sid in enumerate(stream_ids):
            self.rows.append({"channel_id": channel_id, "stream_id": sid, "order": order})

    def order_of(self, channel_id):
        return [r["stream_id"] for r in sorted(
            (r for r in self.rows if r["channel_id"] == channel_id), key=lambda r: r["order"])]


@pytest.fixture
def store(plugin_module, monkeypatch):
    s = _FakeStore(custom_ids={900, 901})

    class _Row:
        objects = _FakeManager(s)

        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(plugin_module, "ChannelStream", _Row)
    return s


def _plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


LOG = MagicMock()


def test_overwrite_keeps_custom_stream_last(plugin_module, store):
    store.put(1, [10, 900])
    added, kept = _plugin(plugin_module)._write_channel_streams(1, [20, 21], True, True, LOG)
    assert store.order_of(1) == [20, 21, 900]
    assert (added, kept) == (2, 1)


def test_overwrite_moves_custom_stream_from_middle_to_end(plugin_module, store):
    store.put(1, [10, 900, 11, 901])
    _plugin(plugin_module)._write_channel_streams(1, [20], True, True, LOG)
    assert store.order_of(1) == [20, 900, 901]


def test_custom_stream_in_match_result_written_once(plugin_module, store):
    store.put(1, [10, 900])
    added, kept = _plugin(plugin_module)._write_channel_streams(1, [900, 20], True, True, LOG)
    assert store.order_of(1) == [20, 900]
    assert (added, kept) == (1, 1)


def test_setting_off_deletes_custom_stream_as_before(plugin_module, store):
    store.put(1, [10, 900])
    added, kept = _plugin(plugin_module)._write_channel_streams(1, [20], True, False, LOG)
    assert store.order_of(1) == [20]
    assert kept == 0


def test_overwrite_off_only_adds_missing(plugin_module, store):
    store.put(1, [10, 900])
    added, kept = _plugin(plugin_module)._write_channel_streams(1, [10, 20], False, True, LOG)
    assert sorted(store.order_of(1)) == [10, 20, 900]
    assert added == 1


def test_other_channels_untouched(plugin_module, store):
    store.put(1, [10, 900])
    store.put(2, [30, 901])
    _plugin(plugin_module)._write_channel_streams(1, [20], True, True, LOG)
    assert store.order_of(2) == [30, 901]


def test_failed_custom_read_keeps_running(plugin_module, store, monkeypatch):
    store.put(1, [10, 900])
    real_filter = plugin_module.ChannelStream.objects.filter

    def broken(channel_id=None, stream__is_custom=None):
        if stream__is_custom is not None:
            raise RuntimeError("db down")
        return real_filter(channel_id=channel_id)

    monkeypatch.setattr(plugin_module.ChannelStream.objects, "filter", broken)
    log = MagicMock()
    added, kept = _plugin(plugin_module)._write_channel_streams(1, [20], True, True, log)
    assert store.order_of(1) == [20]
    assert kept == 0
    assert log.warning.called


# --- Sort -----------------------------------------------------------------

def test_split_custom_tail_keeps_order(plugin_module):
    streams = [{"id": 1, "is_custom": False}, {"id": 900, "is_custom": True},
               {"id": 2, "is_custom": False}, {"id": 901, "is_custom": True}]
    regular, custom = plugin_module.Plugin._split_custom_tail(streams)
    assert [s["id"] for s in regular] == [1, 2]
    assert [s["id"] for s in custom] == [900, 901]


def test_split_custom_tail_missing_key_is_regular(plugin_module):
    regular, custom = plugin_module.Plugin._split_custom_tail([{"id": 1}])
    assert [s["id"] for s in regular] == [1] and custom == []


# --- Setting --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, True), (True, True), (False, False), ("false", False), ("true", True), ("0", False),
])
def test_resolve_keep_custom(plugin_module, raw, expected):
    settings = {} if raw is None else {"keep_custom_streams": raw}
    assert plugin_module.Plugin._resolve_keep_custom_streams(settings) is expected


def test_setting_is_in_matching_section(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    ids = [f.get("id") for f in inst.fields]
    assert ids.index("keep_custom_streams") == ids.index("overwrite_streams") + 1
    field = next(f for f in inst.fields if f.get("id") == "keep_custom_streams")
    assert field["type"] == "boolean" and field["default"] is True
