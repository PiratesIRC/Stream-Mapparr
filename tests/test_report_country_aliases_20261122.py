"""Three fixes from reading the 2026-11-22 dry runs.

1. The Match & Assign report listed the GROUP's streams for every channel, so
   "HBO" and "HBO West" both showed East and West feeds while the write,
   routed per zone, was correct.
2. The provider files every Pluto feed under the group "United States", so
   "(PLUTO Brazil) Tastemade" read as US and foreign versions reached US
   channels. A platform tag naming a country now beats the group.
3. Real streams were missed at threshold 95 (ESPN8 The Ocho, ITV1, Crime &
   Investigation, Great Movies, Sky Cinema Sci-Fi); built-in aliases added.
"""
import pathlib

import pytest


# --------------------------------------------------------------------------- #
# 2. Platform tag country
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name,expected", [
    ("(PLUTO Brazil) Tastemade", "BR"),
    ("(PLUTO UK) Dog Whisperer", "UK"),
    ("(PLUTO USA) ABC News Live", "US"),
    ("(PLUTO Denmark) Nosey", "DK"),
    ("(PLUTO Sweden) Nosey", "SE"),
    ("(PLUTO Italy) Pluto TV Cinema", "IT"),
    ("(PLUTO Latin) Tastemade", "LATAM"),
    ("(PLUTO Spain) Cine", "ES"),
    ("PLUTO: History", None),          # bare platform prefix names no country
    ("(US) (CX) Showtime (East)", None),
    ("Tastemade", None),
    ("", None),
])
def test_country_from_platform_tag(plugin_module, name, expected):
    import importlib
    country = importlib.import_module(plugin_module.country_detect.__name__)
    assert country.country_from_platform_tag(name) == expected


def test_the_platform_tag_beats_a_catch_all_group(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    stream = {"name": "(PLUTO Brazil) Tastemade", "channel_group__name": "United States"}
    assert p._stream_country_code(stream) == "BR"
    plain = {"name": "US Tastemade (S)", "channel_group__name": "United States"}
    assert p._stream_country_code(plain) == "US"


def test_a_latin_feed_is_foreign_on_a_us_channel(plugin_module):
    cd = plugin_module.country_detect
    assert cd.classify("US", "LATAM") is cd.FOREIGN
    assert cd.classify(None, "LATAM") is cd.UNKNOWN


# --------------------------------------------------------------------------- #
# 3. Aliases
# --------------------------------------------------------------------------- #

def test_the_new_aliases_are_built_in(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    amap = p._build_alias_map({}, "")
    for key in ("ESPN8: The Ocho", "ITV", "Crime+Investigation", "Great! Movies",
                "Sky Cinema Sci-Fi & Horror HD", "Yahoo Finance", "National Geographic Wild"):
        assert key in amap, key
    assert "ITV1" in amap["ITV"]


def test_itv_takes_itv1_and_not_the_timeshift_or_other_itv_channels(plugin_module, fuzzy_module, tmp_path):
    import logging
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95)
    p._alias_map = p._build_alias_map({}, "")
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s
    names = ["UK: ITV 1", "HEVC FHD ITV1", "UK  ITV+1", "UK : ITV QUIZ FHD", "UK: ITV 2", "UK: ITV 3 HD"]
    streams = [{"id": i, "name": n, "m3u_account": 1} for i, n in enumerate(names)]
    matched, _c, _s, _reason, _db = p._match_streams_to_channel(
        {"id": 1, "name": "ITV"}, streams, logging.getLogger("t"), channels_data=[])
    assert sorted(s["name"] for s in matched) == ["HEVC FHD ITV1", "UK: ITV 1"]


# --------------------------------------------------------------------------- #
# 1. The report lists each channel's own streams
# --------------------------------------------------------------------------- #

def _match_and_assign_source(plugin_module):
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    start = src.index("    def add_streams_to_channels_action")
    return src[start:src.index("\n    def ", start + 10)]


def test_the_report_reads_each_channels_own_list(plugin_module):
    """Source-level: the action needs the ORM and cannot run here. It proves
    the per-channel list is recorded and that the CSV rows read it."""
    body = _match_and_assign_source(plugin_module)
    assert "channel_streams_for_report[channel_id] =" in body
    assert "channel_streams_for_report.get(channel['id']" in body
    assert "'stream_names': '; '.join(self._label_streams(matched_streams, logger))" not in body


def test_the_dry_run_counts_each_channels_own_list(plugin_module):
    body = _match_and_assign_source(plugin_module)
    assert "streams_added = len(streams_for_channel)" in body
    assert "streams_added = len(matched_streams)" not in body
