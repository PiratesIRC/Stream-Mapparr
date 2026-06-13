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
