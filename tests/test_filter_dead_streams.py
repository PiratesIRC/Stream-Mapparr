"""Regression locks for issue #65: the Filter Dead Streams setting never removed anything.

_filter_working_streams read width and height as attributes of the Dispatcharr Stream
model. That model has no such attributes; the data lives in the JSON field stream_stats.
Every stream therefore looked like "no metadata" and was kept. These tests use a fake
Stream model that carries ONLY stream_stats, the same shape the live model has.
"""

from unittest.mock import MagicMock


class _FakeRow:
    """A row from filter(id=...).first(): stream_stats and nothing else."""

    def __init__(self, stream_id, stream_stats):
        self.id = stream_id
        self.stream_stats = stream_stats


class _FakeQuerySet:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def values_list(self, *fields, flat=False):
        assert fields == ("id", "stream_stats"), fields
        return [(r.id, r.stream_stats) for r in self._rows]


class _FakeManager:
    def __init__(self, stats_by_id, raise_exc=None):
        self._stats = stats_by_id
        self._raise = raise_exc
        self.filter_calls = []

    def filter(self, **kwargs):
        self.filter_calls.append(kwargs)
        if self._raise is not None:
            raise self._raise
        if "id__in" in kwargs:
            ids = kwargs["id__in"]
            rows = [_FakeRow(i, self._stats[i]) for i in ids if i in self._stats]
        else:
            sid = kwargs["id"]
            rows = [_FakeRow(sid, self._stats[sid])] if sid in self._stats else []
        return _FakeQuerySet(rows)


class _FakeStream:
    def __init__(self, stats_by_id, raise_exc=None):
        self.objects = _FakeManager(stats_by_id, raise_exc)


def _plugin(plugin_module):
    P = plugin_module.Plugin
    return P.__new__(P)


def _streams(ids):
    return [{"id": i, "name": f"Stream {i}"} for i in ids]


def _dead(plugin_module, stats):
    return plugin_module.Plugin._stream_stats_says_dead(stats)


# ---------------------------------------------------------------- 1. pure helper

def test_helper_none_is_not_dead(plugin_module):
    assert _dead(plugin_module, None) is False


def test_helper_empty_dict_is_dead(plugin_module):
    """IPTV Checker writes exactly {} for a stream it found dead."""
    assert _dead(plugin_module, {}) is True


def test_helper_zero_width_and_height_is_dead(plugin_module):
    assert _dead(plugin_module, {"width": 0, "height": 0, "resolution": "0x0"}) is True


def test_helper_real_dimensions_are_not_dead(plugin_module):
    assert _dead(plugin_module, {"width": 1920, "height": 1080, "resolution": "1920x1080"}) is False


def test_helper_resolution_alone_nonzero_is_not_dead(plugin_module):
    """Dispatcharr writes 'resolution' on play but not width/height."""
    assert _dead(plugin_module, {"resolution": "1280x720"}) is False


def test_helper_resolution_0x0_is_dead(plugin_module):
    assert _dead(plugin_module, {"resolution": "0x0"}) is True


def test_helper_audio_only_is_not_dead(plugin_module):
    assert _dead(plugin_module, {"audio_codec": "aac"}) is False


def test_helper_garbage_string_is_not_dead(plugin_module):
    assert _dead(plugin_module, "garbage") is False


def test_helper_none_width_and_height_is_not_dead(plugin_module):
    assert _dead(plugin_module, {"width": None, "height": None}) is False


def test_helper_unparseable_resolution_is_not_dead(plugin_module):
    assert _dead(plugin_module, {"resolution": "HD"}) is False


# ------------------------------------------------- 2. end to end, stats only

def _mixed_stats():
    return {
        1: None,                                                   # never checked: keep
        2: {},                                                     # IPTV Checker dead: drop
        3: {"width": 0, "height": 0, "resolution": "0x0"},         # dead: drop
        4: {"width": 1920, "height": 1080, "resolution": "1920x1080"},  # keep
        5: {"resolution": "1280x720", "video_codec": "h264"},      # Dispatcharr played it: keep
        6: {"audio_codec": "aac"},                                 # no evidence: keep
        # 7 is absent from the database: keep
    }


def test_filter_removes_dead_and_keeps_live_and_unknown(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module, "Stream", _FakeStream(_mixed_stats()))
    inst = _plugin(plugin_module)
    logger = MagicMock()

    result = inst._filter_working_streams(_streams([1, 2, 3, 4, 5, 6, 7]), logger)

    assert [s["id"] for s in result] == [1, 4, 5, 6, 7]


# ---------------------------------------------------------------- 3. the guard

def test_guard_all_dead_returns_original_list(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module, "Stream", _FakeStream({
        10: {}, 11: {"width": 0, "height": 0}, 12: {"resolution": "0x0"},
    }))
    inst = _plugin(plugin_module)
    logger = MagicMock()
    original = _streams([10, 11, 12])

    result = inst._filter_working_streams(original, logger)

    assert result == original
    warned = " ".join(str(c.args[0]) for c in logger.warning.call_args_list)
    assert "skipped" in warned.lower()


# ------------------------------------------------------- 4. one ORM query

def test_filter_makes_one_orm_query_not_one_per_stream(plugin_module, monkeypatch):
    fake = _FakeStream(_mixed_stats())
    monkeypatch.setattr(plugin_module, "Stream", fake)
    inst = _plugin(plugin_module)

    inst._filter_working_streams(_streams([1, 2, 3, 4, 5, 6, 7]), MagicMock())

    assert len(fake.objects.filter_calls) == 1
    assert set(fake.objects.filter_calls[0]["id__in"]) == {1, 2, 3, 4, 5, 6, 7}


# ----------------------------------------------------- 5. ORM exception

def test_orm_exception_returns_original_list(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module, "Stream",
                        _FakeStream(_mixed_stats(), raise_exc=RuntimeError("db down")))
    inst = _plugin(plugin_module)
    original = _streams([1, 2, 3])

    result = inst._filter_working_streams(original, MagicMock())

    assert result == original


# ------------------------------------------- 6. large libraries are chunked

def test_large_stream_list_is_queried_in_chunks(plugin_module, monkeypatch):
    """PostgreSQL refuses a query with more than 65,535 bound parameters. One
    id__in over a very large library would raise, the filter would fail open,
    and dead streams would be kept silently. Ids are queried in chunks."""
    chunk = plugin_module.PluginConfig.DEAD_FILTER_QUERY_CHUNK
    total = chunk * 2 + 1
    stats = {i: {} for i in range(1, total + 1)}
    stats[total] = {"width": 1920, "height": 1080}
    fake = _FakeStream(stats)
    monkeypatch.setattr(plugin_module, "Stream", fake)
    inst = _plugin(plugin_module)

    result = inst._filter_working_streams(_streams(range(1, total + 1)), MagicMock())

    assert len(fake.objects.filter_calls) == 3
    assert all(len(c["id__in"]) <= chunk for c in fake.objects.filter_calls)
    assert chunk < 65535
    assert [s["id"] for s in result] == [total]
