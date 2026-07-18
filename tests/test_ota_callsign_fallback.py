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
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

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
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

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


# --------------------------------------------------------------------------- #
# bug-098 CORE hardening: the DB-rescue of a common-word callsign (KING/WHO/
# WOLF...) is confined to PARENTHESIZED positions and to the OTA branded
# "<callsign> <number>" form (KING 5 / WAVE 3 / WOOD TV8 / WHO 13). A bare
# common word at end-of-name ("WOLF KING", "Doctor Who") or as a loose word
# followed by ordinary text ("King of the Hill") no longer extracts a callsign,
# so a dropped subclass override can never reintroduce bug-098.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,expected", [
    ("NBC - WA Seattle (KING)", "KING"),   # parenthesized -> always rescued
    ("KING 5", "KING"),                     # branded number form preserved
    ("WAVE 3", "WAVE"),
    ("WOOD TV8", "WOOD"),
    ("WHO 13", "WHO"),
    ("WFAA", "WFAA"),                       # non-denylisted callsign unaffected
])
def test_common_word_callsign_rescued_only_in_station_context(real_matcher, name, expected):
    assert real_matcher.extract_callsign(name) == expected


@pytest.mark.parametrize("name", [
    "24/7 KING OF THE HILL",   # loose word + ordinary text
    "THE KING OF QUEENS",
    "WOLF KING",               # denylisted word at end-of-name
    "Doctor Who",              # denylisted word at end-of-name
    "Classic Doctor Who",
    "Teen Wolf",
    "24/7 WILL FERRELL MOVIES",
])
def test_common_word_in_plain_text_extracts_no_callsign(real_matcher, name):
    assert real_matcher.extract_callsign(name) is None


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


# --------------------------------------------------------------------------- #
# bug-098: common-word callsigns (KING/WHO/WOLF/WAVE...) must require
# corroboration before assigning a stream. The denylist normally blocks these
# words, but a real station (KING-TV Seattle) is rescued into channel_lookup,
# and the OTA assignment loop did a bare word-boundary search for the callsign
# -- so "KING OF THE HILL", "DOCTOR WHO", "WOLF KING" were vacuumed onto the
# affiliate channel. Real OTA streams always carry the network affiliation
# and/or community city; the false positives carry neither.
# --------------------------------------------------------------------------- #

def test_common_word_callsign_rejects_unrelated_streams(plugin_module, real_matcher):
    """An OTA channel whose callsign is a common English word must keep only the
    streams that corroborate the station (network/city/paren-callsign) and drop
    unrelated streams that merely contain the word."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

    channel = {"id": 1, "name": "NBC - WA Seattle (KING)"}
    streams = [
        {"id": 10, "name": "CITY: NBC KING SEATTLE", "m3u_account": 1},       # legit: network+city
        {"id": 11, "name": "US: NBC 5 (KING) SEATTLE (A)", "m3u_account": 1}, # legit: paren+network+city
        {"id": 12, "name": "US: 24/7 KING OF THE HILL", "m3u_account": 1},    # false: cartoon
        {"id": 13, "name": "US: THE KING OF QUEENS 4K", "m3u_account": 1},    # false: sitcom
        {"id": 14, "name": "US: WOLF KING", "m3u_account": 1},                # false: movie
    ]

    matched, _c, _s, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])

    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [10, 11]


def test_common_word_callsign_keeps_affiliate_with_other_network(plugin_module, real_matcher):
    """WOLF-TV is a real FOX affiliate, so a stream carrying its network ('FOX
    56 WOLF') is corroborated and kept, while 'TEEN WOLF' / 'DARK WOLF' are
    dropped."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

    channel = {"id": 2, "name": "FOX - PA Scranton (WOLF)"}
    streams = [
        {"id": 20, "name": "US: FOX 56 WOLF HD", "m3u_account": 1},               # legit: network
        {"id": 21, "name": "PRIME: TEEN WOLF", "m3u_account": 1},                 # false
        {"id": 22, "name": "US: THE TERMINAL LIST DARK WOLF", "m3u_account": 1},  # false
    ]

    matched, _c, _s, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])

    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [20]


def test_resolve_ota_callsign_common_word_requires_high_confidence(plugin_module, real_matcher):
    """A common-word callsign only marks a channel as OTA when it is a
    high-confidence extraction (parenthesized/suffixed/end-of-name). A loose
    word in a non-station name must NOT resolve to the affiliate. bug-098."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    # Parenthesized (high-confidence) -> real OTA station.
    assert p._resolve_ota_callsign("NBC - WA Seattle (KING)") == "KING"
    # Loose word in an event/show name -> NOT OTA.
    assert p._resolve_ota_callsign("US: 24/7 KING OF THE HILL") is None
    assert p._resolve_ota_callsign("US: 24/7 WILL FERRELL MOVIES") is None


def test_event_channel_with_common_word_name_not_grabbed_by_affiliate(plugin_module, real_matcher):
    """The cartoon channel 'US: 24/7 KING OF THE HILL' must NOT take the OTA
    path and vacuum the real KING-TV Seattle NBC streams; it falls through to
    fuzzy matching and keeps its own stream. bug-098."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

    channel = {"id": 1, "name": "US: 24/7 KING OF THE HILL"}
    streams = [
        {"id": 10, "name": "US: 24/7 KING OF THE HILL", "m3u_account": 1},  # its own (fuzzy-exact)
        {"id": 11, "name": "CITY: NBC KING SEATTLE", "m3u_account": 1},     # KING-TV affiliate, foreign
        {"id": 12, "name": "US: NBC 5 (KING) SEATTLE (A)", "m3u_account": 1},  # foreign
    ]

    matched, _c, _s, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])

    assert reason != "Callsign match"
    assert [s["id"] for s in matched] == [10]


def test_normal_callsign_unaffected_by_corroboration_guard(plugin_module, real_matcher):
    """A non-common-word callsign (WFAA) keeps the fast bare-word match -- no
    corroboration required, so a plain 'ABC WFAA MIAMI' stream still matches."""
    p = _bare_plugin(plugin_module)
    p.fuzzy_matcher = real_matcher
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

    channel = {"id": 3, "name": "ABC - TX Dallas (WFAA)"}
    streams = [
        {"id": 30, "name": "CITY: WFAA", "m3u_account": 1},          # bare callsign, no network/city
        {"id": 31, "name": "GO: ESPN NEWS", "m3u_account": 1},       # unrelated
    ]

    matched, _c, _s, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])

    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [30]
