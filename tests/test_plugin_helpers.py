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
