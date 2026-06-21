"""Regression tests for bug-063: OTA affiliate channels must match by the
callsign carried in their Dispatcharr name (e.g. "ABC - AL Montgomery (WNCF)")
even when US_channels.json has no broadcast/callsign entry for them.

Before the fix, OTA detection in _match_streams_to_channel depended solely on a
database lookup (_get_channel_info_from_json -> _is_ota_channel). With the
2025-12-08 US database carrying zero callsigns, every affiliate fell through to
strict fuzzy name matching and matched 0 streams -- which, combined with
overwrite_streams=True, wiped existing assignments.
"""

import logging
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"


def _bare_plugin(plugin_module):
    """A Plugin instance without running __init__."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


@pytest.fixture(scope="module")
def real_matcher(fuzzy_module):
    """A FuzzyMatcher loaded from the real plugin dir, so networks.json (the
    FCC OTA station table) is actually loaded into channel_lookup."""
    return fuzzy_module.FuzzyMatcher(plugin_dir=str(PLUGIN_DIR), match_threshold=95)


# --------------------------------------------------------------------------- #
# _extract_paren_callsign — parenthesized callsign only (the reliable signal)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,expected", [
    ("ABC - AL Montgomery (WNCF)", "WNCF"),     # 4-letter
    ("CBS - LA New Orleans (WWL)", "WWL"),       # grandfathered 3-letter
    ("ABC - TX Dallas (WFAA-TV)", "WFAA"),       # suffix stripped
    ("FOX - CA Sacramento (KTXL)", "KTXL"),      # K-prefix
    ("NBC - washington (wrc)", "WRC"),           # case-insensitive
])
def test_extract_paren_callsign_hits(plugin_module, name, expected):
    p = _bare_plugin(plugin_module)
    assert p._extract_paren_callsign(name) == expected


@pytest.mark.parametrize("name", [
    "ESPN",                       # no parens, no callsign
    "The CW",                     # CW is not K/W-prefixed callsign
    "FX (West)",                  # directional false positive guarded
    "Cinemax (East)",             # directional false positive guarded
    "",                           # empty
    None,                         # None
])
def test_extract_paren_callsign_misses(plugin_module, name):
    p = _bare_plugin(plugin_module)
    assert p._extract_paren_callsign(name) is None


# --------------------------------------------------------------------------- #
# _match_streams_to_channel — OTA fallback by name when the DB has no callsign
# --------------------------------------------------------------------------- #

def test_match_ota_by_paren_callsign_without_db_entry(plugin_module, fuzzy_module, tmp_path):
    """The core bug-063 regression: an OTA affiliate with a parenthesized
    callsign matches the stream carrying that callsign, with channels_data empty
    (i.e. no US_channels.json broadcast entry) and the strictest threshold."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95)
    # Isolate downstream quality sort / dedup so we test only the match logic.
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s: s

    channel = {"id": 1, "name": "ABC - TX Dallas (WFAA)"}
    streams = [
        {"id": 10, "name": "CITY: ABC WFAA MIAMI", "m3u_account": 1},  # callsign match
        {"id": 11, "name": "GO: ESPN NEWS", "m3u_account": 1},          # unrelated
        {"id": 12, "name": "CITY: ABC WLS CHICAGO", "m3u_account": 1},  # different callsign
    ]

    matched, _clean_ch, _clean_st, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])

    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [10]


def test_premium_channel_without_callsign_still_uses_fuzzy(plugin_module, fuzzy_module, tmp_path):
    """A channel with no parenthesized callsign must NOT take the OTA path; it
    falls through to fuzzy matching (here an exact name match)."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=80)
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s: s

    channel = {"id": 2, "name": "ESPN"}
    streams = [{"id": 20, "name": "ESPN", "m3u_account": 1}]

    matched, _c, _s, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])

    assert reason != "Callsign match"
    assert [s["id"] for s in matched] == [20]


# --------------------------------------------------------------------------- #
# bug-063 fix #3: networks.json (FCC OTA station table) is loaded and used as
# the authoritative callsign source. Stream-Mapparr already shipped
# networks.json but never loaded it; Channel-Maparr's loader is ported here.
# --------------------------------------------------------------------------- #

def test_networks_json_loaded_into_lookup(real_matcher):
    """The FCC station table populates channel_lookup with thousands of
    callsigns (previously empty because US_channels.json has no broadcast rows)."""
    assert len(real_matcher.channel_lookup) > 1000
    assert "WFAA" in real_matcher.channel_lookup
    assert "WNCF" in real_matcher.channel_lookup


def test_match_broadcast_channel_returns_fcc_station(real_matcher):
    cs, station = real_matcher.match_broadcast_channel("ABC - TX Dallas (WFAA)")
    assert cs == "WFAA"
    assert station is not None
    assert station.get("network_affiliation")            # FCC field present


def test_resolve_ota_callsign_fcc_validated(plugin_module, real_matcher):
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    assert p._resolve_ota_callsign("ABC - TX Dallas (WFAA)") == "WFAA"


def test_resolve_ota_callsign_paren_fallback_when_absent(plugin_module, fuzzy_module, tmp_path):
    """A parenthesized callsign absent from the FCC table still resolves via the
    paren fallback. Uses an empty-lookup matcher (no networks.json) so
    match_broadcast_channel returns (callsign, None) and the fallback kicks in."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95)
    assert not p.fuzzy_matcher.channel_lookup                  # empty -> no FCC validation
    assert p._resolve_ota_callsign("ABC - Smalltown (WZQX)") == "WZQX"


def test_resolve_ota_callsign_none_for_premium(plugin_module, real_matcher):
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    assert p._resolve_ota_callsign("ESPN") is None
    assert p._resolve_ota_callsign("FX (West)") is None


def test_build_us_callsign_database_from_networks(plugin_module):
    """match_us_ota_only's callsign DB is now sourced from networks.json, so it
    is populated even though US_channels.json carries no broadcast entries."""
    p = _bare_plugin(plugin_module)
    db = p._build_us_callsign_database(logging.getLogger("test"))
    assert len(db) > 1000
    assert "WFAA" in db
    assert "WNCF" in db
