"""Issue #62, second report: a channel named "NebulaOne+ Event 12 HD (WEB)" was
read as an OTA station with callsign WEB.

_extract_paren_callsign accepts any parenthesized K/W word of three or four
letters, so "(WEB)" resolved to the callsign WEB. The callsign branch of
_match_streams_to_channel then collected every stream containing the word WEB
and returned before the number check, so each of the 20 numbered event channels
received all 20 event streams, at every threshold.

Two rules close it:
- the user's Ignore Tags are removed from the channel name before a callsign is
  looked for, so a tag the user declared is never a station;
- a THREE-letter parenthesized callsign is accepted only when the FCC station
  table (networks.json) lists it. Real three-letter callsigns are grandfathered
  and all present there (WGN, WWL, KYW, ...); WEB is not.
"""
import logging
import re
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"
LOGGER = logging.getLogger("test_tag_callsign")
KEEP_ALL = dict(ignore_quality=False, ignore_regional=False,
                ignore_geographic=False, ignore_misc=False)


def _bare_plugin(plugin_module, matcher=None):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = matcher
    return p


@pytest.fixture(scope="module")
def real_matcher_factory(fuzzy_module):
    def make(threshold):
        return fuzzy_module.FuzzyMatcher(plugin_dir=str(PLUGIN_DIR), match_threshold=threshold)
    return make


def _streams(fmt):
    return [{"id": n, "name": fmt % n, "channel_group__name": None,
             "m3u_account": 1, "url": "http://example/s%d" % n} for n in range(1, 21)]


@pytest.mark.parametrize("threshold", [80, 95])
@pytest.mark.parametrize("fmt", ["NebulaOne+ Event %d HD (WEB)", "NebulaOne Event %d HD (WEB)"])
@pytest.mark.parametrize("number", [1, 2, 3, 12, 20])
def test_reporter_names_collect_only_their_own_stream(plugin_module, real_matcher_factory,
                                                      threshold, fmt, number):
    p = _bare_plugin(plugin_module, real_matcher_factory(threshold))
    ignore = plugin_module.Plugin._parse_tags("HD, (WEB)")
    matched, _, _, match_type, _ = p._match_streams_to_channel(
        {"id": number, "name": fmt % number, "channel_group__name": None},
        _streams(fmt), LOGGER, ignore_tags=ignore, channels_data=[], **KEEP_ALL)
    assert [s["name"] for s in matched] == [fmt % number]
    assert match_type != "Callsign match"


def test_three_letter_word_absent_from_fcc_table_is_not_a_callsign(plugin_module, real_matcher_factory):
    """Without any Ignore Tags, the FCC rule alone refuses (WEB)."""
    p = _bare_plugin(plugin_module, real_matcher_factory(95))
    assert p._resolve_ota_callsign("NebulaOne+ Event 12 HD (WEB)") is None


@pytest.mark.parametrize("name,expected", [
    ("CBS - LA New Orleans (WWL)", "WWL"),
    ("CW - Chicago (WGN)", "WGN"),
    ("NBC - washington (wrc)", "WRC"),
    ("CBS - PA Philadelphia (KYW)", "KYW"),
])
def test_real_three_letter_callsigns_still_resolve(plugin_module, real_matcher_factory, name, expected):
    p = _bare_plugin(plugin_module, real_matcher_factory(95))
    assert p._resolve_ota_callsign(name) == expected


def test_four_letter_callsign_absent_from_fcc_table_still_resolves(plugin_module, real_matcher_factory):
    """The FCC rule applies to three-letter words only; an unlisted four-letter
    station keeps the bug-063 fallback."""
    p = _bare_plugin(plugin_module, real_matcher_factory(95))
    assert p._resolve_ota_callsign("ABC - Smalltown (WZQX)") == "WZQX"


def test_ignore_tag_is_never_a_callsign(plugin_module, fuzzy_module, tmp_path):
    p = _bare_plugin(plugin_module, fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95))
    assert p._resolve_ota_callsign("Feed (WZQX)") == "WZQX"
    assert p._resolve_ota_callsign("Feed (WZQX)", ignore_tags=["(WZQX)"]) is None
    assert p._channel_ota_callsign({"name": "Feed (WZQX)"}, None, ignore_tags=["(WZQX)"]) is None
    # A tag that does not cover the callsign changes nothing.
    assert p._resolve_ota_callsign("ABC - Smalltown (WZQX)", ignore_tags=["HD", "(WEB)"]) == "WZQX"


def test_fcc_rule_is_skipped_when_the_station_table_is_not_loaded(plugin_module, fuzzy_module, tmp_path):
    """No networks.json means no way to tell WEB from WGN. The fallback keeps its
    old behaviour rather than refusing every three-letter station."""
    p = _bare_plugin(plugin_module, fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95))
    assert p._resolve_ota_callsign("CBS - LA New Orleans (WWL)") == "WWL"


def test_every_ota_decision_receives_the_ignore_tags():
    """Every site deciding whether a channel is OTA must reach the same answer
    (bug-161 review D1), so each call must pass the Ignore Tags."""
    src = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    for helper in ("_channel_ota_callsign", "_same_country_ids_for", "_group_key_for_channel"):
        calls = [m.start() for m in re.finditer(r"self\.%s\(" % helper, src)]
        assert calls, helper
        for start in calls:
            depth, i = 0, src.index("(", start)
            while True:
                depth += {"(": 1, ")": -1}.get(src[i], 0)
                if depth == 0:
                    break
                i += 1
            assert "ignore_tags=" in src[start:i], (helper, src[start:i])
