"""Issue #75: Sort Alternate Streams cleared channel catch-up flags.

Dispatcharr keeps Channel.is_catchup and Channel.catchup_days in step with the
channel's streams through a post_save/post_delete hook on ChannelStream
(update_channel_catchup_fields, apps/channels/signals.py). bulk_create sends no
post_save, so deleting every row and bulk-creating them again left the flags
at False/0: the delete ran the rollup on an empty channel and nothing ran it
again. Dispatcharr also lists no archive stream for a channel whose flag is
False (get_channel_catchup_streams), so archive playback stopped as well.

The fix: Sort changes ChannelStream.order on the existing rows (bulk_update)
and deletes only the rows it drops, and every write path then calls
Dispatcharr's own rollup_catchup_for_channels for the channel. The rollup is a
backstop: a Dispatcharr without it, or a rollup that raises, logs a warning and
never undoes or blocks the write.
"""
import sys
import types
from unittest.mock import MagicMock

import pytest


class _Atomic:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Row:
    """One ChannelStream row with a stable primary key."""
    _next_pk = 1

    def __init__(self, channel_id=None, stream_id=None, order=0, **kw):
        self.pk = _Row._next_pk
        _Row._next_pk += 1
        self.channel_id, self.stream_id, self.order = channel_id, stream_id, order


class _QS:
    def __init__(self, store, kw):
        self.store, self.kw = store, kw

    def _rows(self):
        rows = [r for r in self.store.rows if r.channel_id == self.kw.get("channel_id")]
        if "stream_id__in" in self.kw:
            rows = [r for r in rows if r.stream_id in self.kw["stream_id__in"]]
        if "pk__in" in self.kw:
            rows = [r for r in rows if r.pk in self.kw["pk__in"]]
        return rows

    def select_for_update(self):
        return self

    def order_by(self, *a):
        return _QS(self.store, self.kw)

    def __iter__(self):
        return iter(sorted(self._rows(), key=lambda r: r.order))

    def values_list(self, field, flat=False):
        return [getattr(r, field) for r in sorted(self._rows(), key=lambda r: r.order)]

    def delete(self):
        gone = {r.pk for r in self._rows()}
        self.store.deleted.extend(r.stream_id for r in self.store.rows if r.pk in gone)
        self.store.rows[:] = [r for r in self.store.rows if r.pk not in gone]


class _Manager:
    def __init__(self, store):
        self.store = store

    def select_for_update(self):
        return self

    def filter(self, **kw):
        return _QS(self.store, kw)

    def bulk_create(self, objs):
        self.store.rows.extend(objs)

    def bulk_update(self, objs, fields):
        self.store.bulk_updates.append((list(objs), list(fields)))


class _Store:
    def __init__(self):
        self.rows, self.deleted, self.bulk_updates = [], [], []

    def put(self, channel_id, stream_ids):
        for order, sid in enumerate(stream_ids):
            self.rows.append(_Row(channel_id=channel_id, stream_id=sid, order=order))

    def order_of(self, channel_id):
        return [r.stream_id for r in sorted(
            (r for r in self.rows if r.channel_id == channel_id), key=lambda r: r.order)]

    def pks_of(self, channel_id):
        return {r.stream_id: r.pk for r in self.rows if r.channel_id == channel_id}


@pytest.fixture
def store(plugin_module, monkeypatch):
    s = _Store()

    class FakeChannelStream(_Row):
        objects = _Manager(s)

    monkeypatch.setattr(plugin_module, "ChannelStream", FakeChannelStream)
    monkeypatch.setattr(sys.modules["django.db"], "transaction",
                        types.SimpleNamespace(atomic=lambda: _Atomic()), raising=False)
    return s


@pytest.fixture
def rollup(monkeypatch):
    calls = []
    utils = types.ModuleType("apps.channels.utils")
    utils.rollup_catchup_for_channels = lambda ids: calls.append(list(ids))
    monkeypatch.setitem(sys.modules, "apps.channels.utils", utils)
    return calls


def _plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --- the rollup backstop itself ---

def test_rollup_calls_dispatcharr_helper(plugin_module, rollup):
    assert _plugin(plugin_module)._rollup_catchup([5, 6], MagicMock()) is True
    assert rollup == [[5, 6]]


def test_rollup_missing_in_dispatcharr_warns_and_returns_false(plugin_module, monkeypatch):
    utils = types.ModuleType("apps.channels.utils")  # an older Dispatcharr: no helper
    monkeypatch.setitem(sys.modules, "apps.channels.utils", utils)
    log = MagicMock()
    assert _plugin(plugin_module)._rollup_catchup([5], log) is False
    assert log.warning.called


def test_rollup_that_raises_warns_and_does_not_raise(plugin_module, monkeypatch):
    utils = types.ModuleType("apps.channels.utils")

    def boom(ids):
        raise RuntimeError("db gone")
    utils.rollup_catchup_for_channels = boom
    monkeypatch.setitem(sys.modules, "apps.channels.utils", utils)
    log = MagicMock()
    assert _plugin(plugin_module)._rollup_catchup([5], log) is False
    assert "db gone" in str(log.warning.call_args)


def test_rollup_with_no_channels_does_nothing(plugin_module, rollup):
    assert _plugin(plugin_module)._rollup_catchup([], MagicMock()) is True
    assert rollup == []


# --- Sort writes the new order in place ---

def test_sort_reorders_rows_in_place_without_deleting(plugin_module, store, rollup):
    store.put(1, [10, 11, 12])
    pks_before = store.pks_of(1)
    result = _plugin(plugin_module)._apply_sorted_order(1, [10, 11, 12], [12, 10, 11], MagicMock())
    assert result is True
    assert store.deleted == []
    (objs, fields), = store.bulk_updates
    assert fields == ["order"]
    assert store.order_of(1) == [12, 10, 11]
    assert store.pks_of(1) == pks_before  # same rows, so the delete hook never ran
    assert rollup == [[1]]


def test_sort_updates_only_rows_whose_position_moved(plugin_module, store, rollup):
    store.put(1, [10, 11, 12])
    _plugin(plugin_module)._apply_sorted_order(1, [10, 11, 12], [10, 12, 11], MagicMock())
    (objs, _), = store.bulk_updates
    assert sorted(o.stream_id for o in objs) == [11, 12]


def test_sort_deletes_only_the_streams_it_drops(plugin_module, store, rollup):
    # _streams_for_channel drops the opposite-zone stream (13) from a West channel.
    store.put(1, [10, 13, 11])
    _plugin(plugin_module)._apply_sorted_order(1, [10, 13, 11], [11, 10], MagicMock())
    assert store.deleted == [13]
    assert store.order_of(1) == [11, 10]
    assert rollup == [[1]]


def test_sort_leaves_a_channel_changed_since_it_was_read(plugin_module, store, rollup):
    store.put(1, [10, 11, 14])  # something added 14 after Sort read [10, 11]
    result = _plugin(plugin_module)._apply_sorted_order(1, [10, 11], [11, 10], MagicMock())
    assert result is False
    assert store.order_of(1) == [10, 11, 14]
    assert store.bulk_updates == [] and store.deleted == []
    assert rollup == []


def test_sort_write_survives_a_missing_rollup(plugin_module, store, monkeypatch):
    monkeypatch.setitem(sys.modules, "apps.channels.utils", types.ModuleType("apps.channels.utils"))
    store.put(1, [10, 11])
    assert _plugin(plugin_module)._apply_sorted_order(1, [10, 11], [11, 10], MagicMock()) is True
    assert store.order_of(1) == [11, 10]


def test_sort_action_no_longer_bulk_creates(plugin_module):
    import inspect
    src = inspect.getsource(plugin_module.Plugin.sort_streams_action)
    assert "ChannelStream.objects.bulk_create" not in src
    assert "_apply_sorted_order" in src


# --- Match and Assign writes run the rollup ---

def test_overwrite_write_rolls_up(plugin_module, store, rollup):
    store.put(1, [10])
    _plugin(plugin_module)._write_channel_streams(1, [20, 21], True, False, MagicMock())
    assert store.order_of(1) == [20, 21]
    assert rollup == [[1]]


def test_additive_write_rolls_up_when_it_adds(plugin_module, store, rollup):
    # bulk_create of a new archive stream sends no post_save, so without the
    # rollup the channel would never gain its catch-up flag.
    store.put(1, [10])
    _plugin(plugin_module)._write_channel_streams(1, [10, 20], False, False, MagicMock())
    assert rollup == [[1]]


def test_additive_write_that_adds_nothing_skips_rollup(plugin_module, store, rollup):
    store.put(1, [10])
    _plugin(plugin_module)._write_channel_streams(1, [10], False, False, MagicMock())
    assert rollup == []
