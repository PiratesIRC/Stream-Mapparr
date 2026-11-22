"""Call-letter matches that name another network (measured 2026-11-22).

A US station often carries a second network on a subchannel under the same
call letters: WEVV is CBS on its main channel and FOX on DT2, KALB is NBC with
CBS on a subchannel. The call-letter path accepted every stream containing the
call letters, so a Match & Assign over group US: CBS added 77 streams labelled
FOX, ABC, NBC, CW or MY to CBS channels. Stream names below are real names from
that run's report.
"""
import logging

import pytest


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


@pytest.mark.parametrize("name,expected", [
    ("CBS - IN Evansville (WEVV)", {"CBS"}),
    ("US FOX 44 (WEVV DT2) Evansville (H)", {"FOX"}),
    ("US: MY 38 Boston (WSBK)", {"MY"}),
    ("US MyTV (KGBT DT) Mission (H)", {"MY"}),
    ("(US) (FOX1) WSBT FOX (WSBTDT2)", {"FOX"}),
    ("US: CBS 19 (WOIO) SHAKER HEIGHTS HD", {"CBS"}),
    ("US CBS 8 (WAKA) Selma (H)", {"CBS"}),
    ("Fox Sports 1", {"FOX"}),
    ("CNBC", set()),          # a network label inside a longer word is not one
    ("ABCDEF", set()),
    ("", set()),
])
def test_networks_named(plugin_module, name, expected):
    assert plugin_module.networks_named(name) == expected


@pytest.mark.parametrize("channel,stream,expected", [
    ("CBS - IN Evansville (WEVV)", "US FOX 44 (WEVV DT2) Evansville (H)", True),
    ("CBS - LA Alexandria (KALB)", "US NBC 5 (KALB) Alexandria (H)", True),
    ("CBS - MS Biloxi (WLOX)", "ABC: BILOXI WLOX", True),
    ("CBS - CA Chico (KHSL)", "US CW 12 (KHSL LD2) Chico/Redding (H)", True),
    ("CBS - IN Evansville (WEVV)", "US: CBS 44 (WEVV) EVANSVILLE HD", False),
    ("CBS - IN South Bend (WSBT)", "PRIME: CBS SOUTH BEND NEWS (WSBT)", False),
    ("CBS - OH Cleveland (WOIO)", "NFL Teams: CBS Browns (WOIO) Clevland OH", False),
    ("CBS - AL Birmingham (WIAT)", "US CBS (WIAT) (D)", False),
    ("CBS - AL Birmingham (WIAT)", "WIAT BIRMINGHAM", False),          # names no network
    ("CBS - IN South Bend (WSBT)", "WSBT CBS / FOX SOUTH BEND", False),  # names both
    ("WEVV Evansville", "US FOX 44 (WEVV DT2)", False),                # channel names none
    ("CBS FOX Simulcast (WXXX)", "US FOX (WXXX)", False),              # channel names two
])
def test_network_conflicts(plugin_module, channel, stream, expected):
    assert plugin_module.network_conflicts(channel, stream) is expected


def test_conflicting_streams_are_dropped(plugin_module):
    streams = [{"id": 1, "name": "US: CBS 44 (WEVV) EVANSVILLE HD"},
               {"id": 2, "name": "US FOX 44 (WEVV DT2) Evansville (H)"},
               {"id": 3, "name": "FOX: EVANSVILLE IN WEVV"}]
    kept = _bare(plugin_module)._drop_network_conflicts(
        "CBS - IN Evansville (WEVV)", streams, logging.getLogger("t"))
    assert [s["id"] for s in kept] == [1]


def test_all_conflicting_keeps_every_stream(plugin_module):
    """Fail open, like zones: a station whose streams all carry another
    network's label keeps them rather than ending with none."""
    streams = [{"id": 1, "name": "US : CW   CA   LOS ANGELES (KCAL)"}]
    kept = _bare(plugin_module)._drop_network_conflicts(
        "CBS - CA Los Angeles (KCAL)", streams, logging.getLogger("t"))
    assert kept == streams


def test_the_call_letter_match_drops_the_other_network(plugin_module, fuzzy_module, tmp_path):
    """End to end through _match_streams_to_channel. No shipped database has a
    callsign entry, so the parenthesized callsign is the production path
    (bug-161): channels_data is empty here on purpose."""
    p = _bare(plugin_module)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95)
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s

    channel = {"id": 1, "name": "CBS - NM Albuquerque (KRQE)"}
    streams = [
        {"id": 10, "name": "US: CBS 13 (KRQE) ALBUQUERQUE HD", "m3u_account": 1},
        {"id": 11, "name": "US FOX (KRQE) Albuquerque Santa Fe (H)", "m3u_account": 1},
        {"id": 12, "name": "FOX: ALBUQUERQUE NM KRQE", "m3u_account": 1},
        {"id": 13, "name": "KRQE NEWS 13", "m3u_account": 1},
    ]
    matched, _c, _s, reason, _db = p._match_streams_to_channel(
        channel, streams, logging.getLogger("test"), channels_data=[])
    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [10, 13]
