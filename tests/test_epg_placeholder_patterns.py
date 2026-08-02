"""Tests for _resolve_epg_placeholder_patterns / _is_epg_placeholder_name, and
for the per-run EPG title cache added to _resolve_current_epg_title_for_epg_data_id.

Maintainer review finding: _resolve_epg_placeholder_patterns compiled user
input with a bare re.compile(), bypassing the same _pattern_is_unsafe safety
gate the sibling stream_name_regex_rules setting already goes through --
measured live at 3+ seconds for a single 27-char name against ^(a+)+$,
inside a uWSGI/gevent worker that never yields mid-match.
"""
import json

import pytest


def _bare_plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_unsafe_pattern_is_rejected_not_compiled(plugin_module):
    """The exact bypass the review found: a catastrophic-backtracking pattern
    must be rejected by the same gate stream_name_regex_rules already uses,
    not silently compiled and later run against every stream name."""
    p = _bare_plugin(plugin_module)
    compiled = p._resolve_epg_placeholder_patterns(
        {"epg_placeholder_name_patterns": "(a+)+$\n^HBO \\d+$"})
    # Only the safe pattern survives; the unsafe one never reaches re.compile's
    # caller as a usable pattern.
    assert len(compiled) == 1
    assert compiled[0].pattern == r"^HBO \d+$"


def test_overlong_pattern_is_rejected(plugin_module):
    p = _bare_plugin(plugin_module)
    cfg = plugin_module.PluginConfig
    overlong = "^" + "a" * (cfg.REGEX_PATTERN_MAX_LEN + 1) + "$"
    compiled = p._resolve_epg_placeholder_patterns(
        {"epg_placeholder_name_patterns": f"{overlong}\n^HBO \\d+$"})
    assert len(compiled) == 1
    assert compiled[0].pattern == r"^HBO \d+$"


def test_pattern_count_is_capped(plugin_module):
    p = _bare_plugin(plugin_module)
    cfg = plugin_module.PluginConfig
    many = "\n".join(f"^X{i}$" for i in range(cfg.REGEX_RULES_MAX + 5))
    compiled = p._resolve_epg_placeholder_patterns({"epg_placeholder_name_patterns": many})
    assert len(compiled) == cfg.REGEX_RULES_MAX


def test_invalid_regex_syntax_is_skipped(plugin_module):
    p = _bare_plugin(plugin_module)
    compiled = p._resolve_epg_placeholder_patterns(
        {"epg_placeholder_name_patterns": "^HBO ([\n^HBO \\d+$"})
    assert len(compiled) == 1


def test_is_epg_placeholder_name_uses_fullmatch_not_search(plugin_module):
    """Review note: .search() would match an unanchored pattern anywhere in
    the name, which isn't what a setting titled "eligibility" implies. All
    shipped defaults are already ^...$-anchored (fullmatch is a no-op for
    them); this only changes behavior for a hypothetical unanchored
    user-written pattern."""
    p = _bare_plugin(plugin_module)
    patterns = [plugin_module.re.compile("PPV", plugin_module.re.IGNORECASE)]
    assert p._is_epg_placeholder_name("PPV", patterns) is True
    assert p._is_epg_placeholder_name("PPV EVENT 04", patterns) is False


def test_anchored_defaults_unaffected_by_fullmatch(plugin_module):
    p = _bare_plugin(plugin_module)
    patterns = p._resolve_epg_placeholder_patterns(
        {"epg_placeholder_name_patterns": r"^PPV EVENT \d+$"})
    assert p._is_epg_placeholder_name("PPV EVENT 04", patterns) is True
    assert p._is_epg_placeholder_name("XPPV EVENT 04X", patterns) is False


def test_epg_title_cache_hit_short_circuits(plugin_module):
    """A cache hit (including a cached None) returns immediately -- before any
    EPG lookup is even attempted."""
    p = _bare_plugin(plugin_module)
    cache = {7: "Cached Title", 8: None}
    assert p._resolve_current_epg_title_for_epg_data_id(7, [], set(), cache) == "Cached Title"
    assert p._resolve_current_epg_title_for_epg_data_id(8, [], set(), cache) is None


def test_epg_title_cache_prevents_requery(plugin_module, caplog):
    """Review finding: without a per-run cache, the same epg_data_id gets
    re-queried once per channel group that considers it (measured live at
    ~8600 queries in one pass). This environment has no apps.epg.models stub,
    so a real lookup attempt always fails and logs -- used here as a probe:
    the debug log firing only once across two calls with the same id and the
    same cache dict proves the second call short-circuited on the cache
    rather than attempting the lookup again."""
    import logging
    caplog.set_level(logging.DEBUG, logger="plugins.stream_mapparr")
    p = _bare_plugin(plugin_module)
    cache = {}
    p._resolve_current_epg_title_for_epg_data_id(99, [], set(), cache)
    p._resolve_current_epg_title_for_epg_data_id(99, [], set(), cache)
    lookup_attempts = [r for r in caplog.records if "EPG lookup failed" in r.message]
    assert len(lookup_attempts) == 1
    assert cache == {99: None}


def test_epg_title_cache_none_default_preserves_old_behavior(plugin_module):
    """No cache passed (the default) -> old per-call behavior, no crash."""
    p = _bare_plugin(plugin_module)
    assert p._resolve_current_epg_title_for_epg_data_id(7, [], set()) is None
