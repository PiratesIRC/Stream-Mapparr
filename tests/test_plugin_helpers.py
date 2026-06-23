"""Tests for pure helpers in plugin.py.

plugin.py imports Django + the Dispatcharr ORM at module load, so conftest stubs
those before import. We exercise the genuinely pure helpers — the ones whose past
breakage is recorded in .wolf/buglog.json / cerebrum Do-Not-Repeat.
"""

from datetime import datetime, timedelta, timezone as dt_tz

import pytest


# --------------------------------------------------------------------------- #
# _parse_tags — the canonical quote-aware comma parser
# --------------------------------------------------------------------------- #

def test_parse_tags_basic(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags("a, b ,c") == ["a", "b", "c"]


def test_parse_tags_empty(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags("") == []
    assert Plugin._parse_tags("   ") == []
    assert Plugin._parse_tags(None) == []


def test_parse_tags_quoted_comma_is_literal(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags('"a, b", c') == ["a, b", "c"]


def test_parse_tags_drops_empties(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags("a,,b,") == ["a", "b"]


# --------------------------------------------------------------------------- #
# _parse_priority_list — lowercased delegation to _parse_tags
# --------------------------------------------------------------------------- #

def test_parse_priority_list_lowercases(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_priority_list("EAC3, AC3") == ["eac3", "ac3"]


def test_parse_priority_list_empty_inputs(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_priority_list("") == []
    assert Plugin._parse_priority_list(None) == []


# --------------------------------------------------------------------------- #
# _audio_rank — empty list no-op, substring match, unlisted=worst
# --------------------------------------------------------------------------- #

def test_audio_rank_empty_list_is_noop(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._audio_rank("5.1", []) == 0
    assert Plugin._audio_rank(None, []) == 0


def test_audio_rank_returns_index(plugin_module):
    Plugin = plugin_module.Plugin
    pri = ["5.1", "stereo"]
    assert Plugin._audio_rank("5.1", pri) == 0
    assert Plugin._audio_rank("stereo", pri) == 1


def test_audio_rank_substring_match(plugin_module):
    """ffprobe emits both '5.1' and '5.1(side)' for one layout -> substring, not exact."""
    Plugin = plugin_module.Plugin
    assert Plugin._audio_rank("5.1(side)", ["5.1"]) == 0


def test_audio_rank_unlisted_and_missing_sort_last(plugin_module):
    Plugin = plugin_module.Plugin
    pri = ["5.1", "stereo"]
    assert Plugin._audio_rank("mono", pri) == len(pri)
    assert Plugin._audio_rank(None, pri) == len(pri)
    assert Plugin._audio_rank("", pri) == len(pri)


# --------------------------------------------------------------------------- #
# _is_probe_fresh — failed probes (null mbps) are NEVER fresh (bug-008)
# --------------------------------------------------------------------------- #

def _bare_plugin(plugin_module):
    """A Plugin instance without running __init__ (the helper needs no state)."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_probe_fresh_recent_real_measurement(plugin_module):
    p = _bare_plugin(plugin_module)
    now = datetime.now(dt_tz.utc)
    entry = {"throughput_mbps": 12.3, "throughput_measured_at": now.isoformat()}
    assert p._is_probe_fresh(entry, ttl_minutes=30) is True


def test_probe_with_null_mbps_is_never_fresh(plugin_module):
    """bug-008: a fresh timestamp + null mbps must NOT block re-probing."""
    p = _bare_plugin(plugin_module)
    now = datetime.now(dt_tz.utc)
    entry = {"throughput_mbps": None, "throughput_measured_at": now.isoformat()}
    assert p._is_probe_fresh(entry, ttl_minutes=30) is False


def test_probe_expired_is_not_fresh(plugin_module):
    p = _bare_plugin(plugin_module)
    old = datetime.now(dt_tz.utc) - timedelta(minutes=45)
    entry = {"throughput_mbps": 5.0, "throughput_measured_at": old.isoformat()}
    assert p._is_probe_fresh(entry, ttl_minutes=30) is False


def test_probe_missing_or_malformed_entry(plugin_module):
    p = _bare_plugin(plugin_module)
    assert p._is_probe_fresh(None, ttl_minutes=30) is False
    assert p._is_probe_fresh({}, ttl_minutes=30) is False
    assert p._is_probe_fresh(
        {"throughput_mbps": 5.0, "throughput_measured_at": "not-a-date"}, 30) is False


# --------------------------------------------------------------------------- #
# _deduplicate_streams — keyed on (name, m3u_account) for multi-source failover
#   (issue #28 / PR #29). Dedup always runs AFTER _sort_streams_by_quality, so
#   "keep first occurrence" keeps the highest-quality stream per key.
# --------------------------------------------------------------------------- #

def test_dedup_collapses_same_name_same_account(plugin_module):
    """True duplicates within one provider collapse to the first (best) one."""
    p = _bare_plugin(plugin_module)
    streams = [
        {"name": "ESPN HD", "m3u_account": 5, "id": 1},  # best (sorted first)
        {"name": "ESPN HD", "m3u_account": 5, "id": 2},  # dup within same source
    ]
    out = p._deduplicate_streams(streams)
    assert [s["id"] for s in out] == [1]


def test_dedup_drops_nameless_streams(plugin_module):
    p = _bare_plugin(plugin_module)
    streams = [{"name": "", "m3u_account": 5}, {"name": "ESPN HD", "m3u_account": 5}]
    out = p._deduplicate_streams(streams)
    assert [s["name"] for s in out] == ["ESPN HD"]


def test_dedup_keeps_same_name_from_different_accounts(plugin_module):
    """Multi-source failover (issue #28): identical names from DIFFERENT
    providers both survive; only same-source duplicates collapse."""
    p = _bare_plugin(plugin_module)
    streams = [
        {"name": "ESPN HD", "m3u_account": 5, "id": 1},
        {"name": "ESPN HD", "m3u_account": 9, "id": 2},  # different provider
    ]
    out = p._deduplicate_streams(streams)
    assert [s["id"] for s in out] == [1, 2]


# --------------------------------------------------------------------------- #
# _labeled_stream_names — tag CSV stream names with their M3U source so
#   multi-source copies (same name, different provider) are distinguishable.
# --------------------------------------------------------------------------- #

def test_labeled_stream_names_tags_source(plugin_module):
    Plugin = plugin_module.Plugin
    streams = [
        {"name": "GO: CNN", "m3u_account": 5},
        {"name": "GO: CNN", "m3u_account": 9},   # same name, different provider
        {"name": "US: CNN HD", "m3u_account": 5},
    ]
    name_map = {5: "streamq-bk15", 9: "streamq-bk26"}
    out = Plugin._labeled_stream_names(streams, name_map)
    assert out == [
        "GO: CNN [streamq-bk15]",
        "GO: CNN [streamq-bk26]",
        "US: CNN HD [streamq-bk15]",
    ]


def test_labeled_stream_names_unknown_or_missing_account_unlabeled(plugin_module):
    Plugin = plugin_module.Plugin
    streams = [{"name": "Foo", "m3u_account": 99}, {"name": "Bar"}]
    out = Plugin._labeled_stream_names(streams, {5: "x"})
    assert out == ["Foo", "Bar"]  # unknown / missing account -> no tag


# --------------------------------------------------------------------------- #
# Scheduler bootstrap — __init__ must arm the background scheduler (bug-065)
# --------------------------------------------------------------------------- #

def test_init_bootstraps_scheduler_via_load_settings(plugin_module, monkeypatch):
    """bug-065: Dispatcharr re-instantiates the Plugin (calls __init__) but does NOT
    reliably call on_load in this environment, so the scheduler never armed and
    scheduled runs silently stopped. __init__ must bootstrap the scheduler the same
    way the working sibling (event_channel_managarr) does — via _load_settings()."""
    Plugin = plugin_module.Plugin
    calls = []
    monkeypatch.setattr(Plugin, "_load_settings", lambda self: calls.append(True))
    Plugin()
    assert calls == [True], "__init__ must call _load_settings() to arm the scheduler"
