"""Tests for the exact-normalized alias layer (issue: abbreviation/rebrand lift)."""
import pytest


ALIAS_MAP = {
    "FS1": ["FS1", "Fox Sports 1", "FS 1"],
    "WGN America": ["WGN America", "WGN", "NewsNation"],
    "TCM": ["TCM", "Turner Classic Movies"],
    "UP": ["UPtv", "UP TV"],  # canonical 'UP' keyed; bare 'up' NOT a value
}


def test_alias_lookup_exact_normalized_hit(matcher):
    hits = matcher().alias_lookup("FS1", ["Fox Sports 1", "ESPN"], ALIAS_MAP)
    assert hits == ["Fox Sports 1"]


def test_alias_lookup_rebrand_hit(matcher):
    hits = matcher().alias_lookup("WGN America", ["NewsNation HD", "Random"], ALIAS_MAP)
    assert "NewsNation HD" in hits


def test_alias_lookup_numeric_sibling_safe(matcher):
    # FS1's variants never normalize to FS2 — exact matching can't cross siblings.
    hits = matcher().alias_lookup("FS1", ["FS2", "Fox Sports 2"], ALIAS_MAP)
    assert hits == []


def test_alias_lookup_no_entry_returns_empty(matcher):
    assert matcher().alias_lookup("Channel Not In Map", ["FS1"], ALIAS_MAP) == []


def test_alias_lookup_empty_map_returns_empty(matcher):
    assert matcher().alias_lookup("FS1", ["Fox Sports 1"], {}) == []
    assert matcher().alias_lookup("FS1", ["Fox Sports 1"], None) == []


def test_alias_lookup_nospace_match(matcher):
    # "Fox Sports 1" and "FoxSports1" normalize to the same nospace form.
    hits = matcher().alias_lookup("FS1", ["FoxSports1"], ALIAS_MAP)
    assert hits == ["FoxSports1"]


def _bare_plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_build_alias_map_includes_builtin(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({}, None)
    assert "FS1" in m  # from the built-in US table


def test_build_alias_map_merges_custom_object(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '{"My Channel": ["My Stream"]}'}, None)
    assert m["My Channel"] == ["My Stream"]


def test_build_alias_map_custom_bare_string(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '{"X": "Y"}'}, None)
    assert m["X"] == ["Y"]


def test_build_alias_map_custom_wins_and_merges(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '{"FS1": ["Foxy 1"]}'}, None)
    assert "Foxy 1" in m["FS1"] and "Fox Sports 1" in m["FS1"]  # merged, deduped


def test_build_alias_map_malformed_json_ignored(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": "{not json"}, None)
    assert "FS1" in m  # falls back to built-in, no raise


def test_build_alias_map_non_object_ignored(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '["a", "b"]'}, None)
    assert "FS1" in m  # list is not a mapping -> ignored


def _plugin_with_matcher(plugin_module, fuzzy_module, alias_map):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(match_threshold=80)
    p._alias_map = alias_map
    return p


def test_collect_alias_streams_returns_matching_dicts(plugin_module, fuzzy_module):
    p = _plugin_with_matcher(plugin_module, fuzzy_module, {"FS1": ["FS1", "Fox Sports 1"]})
    streams = [{"name": "Fox Sports 1", "id": 1}, {"name": "ESPN", "id": 2}]
    out = p._collect_alias_streams("FS1", streams,
                                   ignore_tags=[], ignore_quality=True, ignore_regional=True,
                                   ignore_geographic=True, ignore_misc=True)
    assert [s["id"] for s in out] == [1]


def test_collect_alias_streams_empty_when_no_matcher(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None
    p._alias_map = {"FS1": ["Fox Sports 1"]}
    out = p._collect_alias_streams("FS1", [{"name": "Fox Sports 1"}],
                                   ignore_tags=[], ignore_quality=True, ignore_regional=True,
                                   ignore_geographic=True, ignore_misc=True)
    assert out == []


def test_alias_only_stream_survives_rematch_filter(plugin_module, fuzzy_module):
    """THE blocker lock: an alias-only stream (FS1 -> 'Fox Sports 1') has low
    channel-name similarity and would be dropped by the re-match loop. It must
    still appear in the final matched set via the alias force-include."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(match_threshold=80)
    p._alias_map = {"FS1": ["FS1", "Fox Sports 1"]}

    channel = {"name": "FS1", "id": 10}
    streams = [{"name": "Fox Sports 1", "id": 1, "m3u_account": 1}]

    # Precondition: fuzzy alone must miss, so the test proves the alias path (not fuzzy).
    assert p.fuzzy_matcher.fuzzy_match("FS1", ["Fox Sports 1"])[0] is None

    result = p._match_streams_to_channel(channel, streams, logger=None)
    matched = result[0]  # (streams, cleaned_channel, cleaned_streams, reason, db)
    assert any(s["id"] == 1 for s in matched), "alias-only stream was dropped"
