"""tests/test_regex_preprocessing.py — issue #36 regex stream-name pre-processing."""
import json

import pytest


def _plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --------------------------------------------------------------------------- #
# Task 1 — resolution gates
# --------------------------------------------------------------------------- #

def _statuses(report):
    return [r["status"] for r in report]


def test_valid_rules_resolve_in_order(plugin_module):
    p = _plugin(plugin_module)
    raw = json.dumps([["\\s*▎\\s*", " "], ["(?i)\\bvip\\b", ""]])
    rules, report = p._resolve_stream_regex_rules({"stream_name_regex_rules": raw})
    assert _statuses(report) == ["ok", "ok"]
    assert len(rules) == 2
    assert rules[0][0].sub(rules[0][1], "UK ▎BBC 1") == "UK BBC 1"


def test_feature_off_when_setting_empty_or_absent(plugin_module):
    p = _plugin(plugin_module)
    for settings in ({}, {"stream_name_regex_rules": ""}, {"stream_name_regex_rules": "   "}, None):
        rules, report = p._resolve_stream_regex_rules(settings)
        assert rules == [] and report == []


def test_malformed_json_disables_feature(plugin_module):
    p = _plugin(plugin_module)
    rules, report = p._resolve_stream_regex_rules({"stream_name_regex_rules": "[[not json"})
    assert rules == []
    assert _statuses(report) == ["invalid_json_shape"]


def test_bad_shapes_skip_only_the_offending_rule(plugin_module):
    p = _plugin(plugin_module)
    raw = json.dumps([["ok", "x"], "not-a-pair", ["one"], [1, 2], ["also ok", ""]])
    rules, report = p._resolve_stream_regex_rules({"stream_name_regex_rules": raw})
    assert _statuses(report) == ["ok", "invalid_json_shape", "invalid_json_shape",
                                 "invalid_json_shape", "ok"]
    assert len(rules) == 2


def test_caps_rule_count_pattern_length_empty_pattern(plugin_module):
    p = _plugin(plugin_module)
    cfg = plugin_module.PluginConfig
    too_many = [["a", "b"]] * (cfg.REGEX_RULES_MAX + 3)
    rules, report = p._resolve_stream_regex_rules(
        {"stream_name_regex_rules": json.dumps(too_many)})
    assert len(rules) == cfg.REGEX_RULES_MAX
    assert _statuses(report).count("too_long") == 3  # over-cap rules reported, skipped

    raw = json.dumps([["x" * (cfg.REGEX_PATTERN_MAX_LEN + 1), ""],
                      ["ok", "y" * (cfg.REGEX_PATTERN_MAX_LEN + 1)],
                      ["", "z"]])
    rules, report = p._resolve_stream_regex_rules({"stream_name_regex_rules": raw})
    assert _statuses(report) == ["too_long", "too_long", "empty_pattern"]
    assert rules == []


def test_compile_error_reported(plugin_module):
    p = _plugin(plugin_module)
    rules, report = p._resolve_stream_regex_rules(
        {"stream_name_regex_rules": json.dumps([["(unclosed", ""]])})
    assert _statuses(report) == ["compile_error"]
    assert "detail" in report[0] and report[0]["detail"]


def test_static_gate_rejects_exponential_class(plugin_module):
    unsafe = plugin_module._pattern_is_unsafe
    for bad in [r"(a+)+$", r"(x*)*", r"(a|ab)+", r"(?:.*)+", r"((a+)b)*"]:
        assert unsafe(bad), bad
    for good in [r"\s*▎\s*", r"(?i)\bvip\b", r"(\d{1,3}\.){3}\d{1,3}",
                 r"^(UK|US)\s*:\s*", r"a+b*c?", r"[abc]+"]:
        assert not unsafe(good), good


def test_static_gate_fails_closed_on_parser_error(plugin_module, monkeypatch):
    # If the private parser API is missing/broken, safety cannot be verified -> unsafe.
    monkeypatch.setattr(plugin_module, "_sre_parse_pattern",
                        lambda text: (_ for _ in ()).throw(RuntimeError("no parser")))
    assert plugin_module._pattern_is_unsafe(r"abc")


def test_unsafe_pattern_status_flows_through_resolver(plugin_module):
    p = _plugin(plugin_module)
    rules, report = p._resolve_stream_regex_rules(
        {"stream_name_regex_rules": json.dumps([["(a+)+$", ""], ["safe", "x"]])})
    assert _statuses(report) == ["unsafe_pattern", "ok"]
    assert len(rules) == 1


def test_non_dict_settings_returns_empty(plugin_module):
    # Non-dict settings (e.g., list, string, number) should gracefully return ([], [])
    p = _plugin(plugin_module)
    rules, report = p._resolve_stream_regex_rules(["not", "a", "dict"])
    assert rules == [] and report == []


def test_static_gate_fails_closed_on_malformed_tree(plugin_module, monkeypatch):
    # If walk() encounters an unexpected tree shape, fail closed -> unsafe.
    monkeypatch.setattr(plugin_module, "_sre_parse_pattern",
                        lambda text: (([("MAX_REPEAT", None)], 4294967295)))
    # walk will raise TypeError trying to unpack: lo, hi, body = None
    assert plugin_module._pattern_is_unsafe(r"abc")


# --------------------------------------------------------------------------- #
# Task 2 — application + containment
# --------------------------------------------------------------------------- #
import re as _re


def _rules(plugin_module, pairs):
    p = _plugin(plugin_module)
    rules, report = p._resolve_stream_regex_rules(
        {"stream_name_regex_rules": json.dumps(pairs)})
    assert all(r["status"] == "ok" for r in report), report
    return rules


def test_apply_sets_match_name_and_counts_changed(plugin_module):
    streams = [{"name": "UK ▎BBC 1 FHD"}, {"name": "CNN"}]
    rules = _rules(plugin_module, [["\\s*▎\\s*", " "]])
    counters = plugin_module._apply_regex_rules_to_streams(streams, rules)
    assert streams[0]["match_name"] == "UK BBC 1 FHD"
    assert streams[1]["match_name"] == "CNN"
    assert counters["changed"] == 1


def test_feature_off_still_sets_match_name(plugin_module):
    streams = [{"name": "BBC 1"}]
    plugin_module._apply_regex_rules_to_streams(streams, [])
    assert streams[0]["match_name"] == "BBC 1"


def test_rules_chain_in_order(plugin_module):
    # second rule matches only the first rule's output
    rules = _rules(plugin_module, [["AAA", "BBB"], ["BBBX", "OK"]])
    streams = [{"name": "AAAX"}]
    plugin_module._apply_regex_rules_to_streams(streams, rules)
    assert streams[0]["match_name"] == "OK"


def test_long_name_skips_preprocessing(plugin_module):
    cfg = plugin_module.PluginConfig
    long_name = "A" * (cfg.REGEX_NAME_MAX_LEN + 1)
    streams = [{"name": long_name}]
    counters = plugin_module._apply_regex_rules_to_streams(
        streams, _rules(plugin_module, [["A", "B"]]))
    assert streams[0]["match_name"] == long_name
    assert counters["skipped_long"] == 1 and counters["changed"] == 0


def test_growth_cap_reverts_and_stops_chaining(plugin_module):
    # one 10x expander is allowed (under floor), chained expanders trip the cap
    rules = _rules(plugin_module, [["x", "x" * 10], ["x", "x" * 10]])
    streams = [{"name": "x" * 200}]  # 200 -> 2000 (over max(4*200,1000)=1000)
    counters = plugin_module._apply_regex_rules_to_streams(streams, rules)
    assert streams[0]["match_name"] == "x" * 200  # reverted to pre-rule value
    assert counters["growth_reverted"] == 1


def test_emptied_name_counted(plugin_module):
    rules = _rules(plugin_module, [["^UK.*", ""]])
    streams = [{"name": "UK BBC 1"}]
    counters = plugin_module._apply_regex_rules_to_streams(streams, rules)
    assert streams[0]["match_name"] == ""
    assert counters["emptied"] == 1


def test_rule_error_falls_back_to_pre_failure_value(plugin_module, monkeypatch):
    rules = _rules(plugin_module, [["B", "C"], ["X", "Y"]])
    boom = _re.compile("X")
    def raising_sub(repl, s):
        raise RuntimeError("boom")
    # replace rule 2 with a raiser to exercise the defensive path
    rules[1] = (type("F", (), {"sub": staticmethod(raising_sub)})(), "Y")
    streams = [{"name": "BX"}]
    counters = plugin_module._apply_regex_rules_to_streams(streams, rules)
    assert streams[0]["match_name"] == "CX"  # rule 1 applied, rule 2's failure absorbed
    assert counters["rule_errors"] == 1


def test_budget_trip_disables_rules_for_rest_of_pass(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "REGEX_PASS_BUDGET_S", 0.0)
    rules = _rules(plugin_module, [["A", "B"]])
    streams = [{"name": "A"}, {"name": "A"}]
    counters = plugin_module._apply_regex_rules_to_streams(streams, rules)
    assert counters["budget_tripped"] is True
    assert streams[1]["match_name"] == "A"  # untouched after the trip


def test_mname_accessor_degrades_to_raw(plugin_module):
    _mname = plugin_module._mname
    assert _mname({"name": "X", "match_name": "Y"}) == "Y"
    assert _mname({"name": "X"}) == "X"          # dict that missed the choke point
    assert _mname({"name": "X", "match_name": ""}) == ""  # emptied stays emptied


# --------------------------------------------------------------------------- #
# Task 3 — settings field + validate integration
# --------------------------------------------------------------------------- #

def test_field_declared(plugin_module):
    p = plugin_module.Plugin()
    ids = [f["id"] for f in p.fields]
    assert "stream_name_regex_rules" in ids
    field = next(f for f in p.fields if f["id"] == "stream_name_regex_rules")
    assert field["type"] == "string" and field["default"] == ""
    # user-surprise guard from the spec: help text must state the split
    assert "quality" in field["help_text"].lower()
    assert "never modified" in field["help_text"].lower()


def test_validate_reports_rules_section(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    lines = p._validate_regex_rules_setting(
        {"stream_name_regex_rules": json.dumps([["ok", ""], ["(a+)+$", ""]])})
    assert len(lines) == 1 and lines[0].startswith("❌")
    assert "1 ok" in lines[0] and "1 rejected" in lines[0]
    assert p._validate_regex_rules_setting({"stream_name_regex_rules": ""}) == []
    good = p._validate_regex_rules_setting(
        {"stream_name_regex_rules": json.dumps([["ok", ""]])})
    assert len(good) == 1 and good[0].startswith("✅")


# --------------------------------------------------------------------------- #
# Task 4 — consumer split + end-to-end
# --------------------------------------------------------------------------- #

def test_dedup_keys_on_raw_name(plugin_module):
    """Two distinct streams whose match_names collide must BOTH survive dedup."""
    p = _plugin(plugin_module)
    streams = [
        {"name": "BBC 1 FHD", "match_name": "BBC 1", "m3u_account": 1, "id": 1},
        {"name": "BBC 1",     "match_name": "BBC 1", "m3u_account": 1, "id": 2},
    ]
    assert len(p._deduplicate_streams(streams)) == 2


def test_alias_roundtrip_uses_match_name_on_both_ends(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    p._alias_map = {"dummy": ["dummy"]}
    captured = {}

    class FakeMatcher:
        def alias_lookup(self, channel_name, stream_names, *a, **k):
            captured["names"] = list(stream_names)
            return [stream_names[0]]  # hit the first TRANSFORMED name

    p.fuzzy_matcher = FakeMatcher()
    streams = [{"name": "UK ▎BBC 1", "match_name": "BBC 1"},
               {"name": "CNN", "match_name": "CNN"}]
    hits = p._collect_alias_streams("BBC 1", streams, [], True, True, True, True)
    assert captured["names"] == ["BBC 1", "CNN"]   # lookup saw transformed names
    assert hits == [streams[0]]                    # recovery found the stream again


def test_end_to_end_junk_stripping_enables_match(plugin_module, fuzzy_module, tmp_path):
    """A name that fails un-regexed matches once the rule strips the junk.
    Uses a junk token that survives normalization (letters glued to the brand)."""
    matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=85)
    raw = "XJUNKX BBC 1"          # 'XJUNKX' is a real token; tanks the token-sort score
    name_after = _re.sub(r"XJUNKX\s*", "", raw)
    m1, s1, _ = matcher.fuzzy_match("BBC 1", [raw], [])
    m2, s2, _ = matcher.fuzzy_match("BBC 1", [name_after], [])
    assert s2 > s1 and m2  # the transformed name matches where the raw one scores lower


def test_choke_point_contract_streams_all_carry_match_name(plugin_module):
    streams = [{"name": f"S{i}"} for i in range(3)]
    plugin_module._apply_regex_rules_to_streams(streams, [])
    assert all("match_name" in s for s in streams)


# --------------------------------------------------------------------------- #
# Task 5 — test_regex_rules action
# --------------------------------------------------------------------------- #

def test_escape_invisibles(plugin_module):
    esc = plugin_module._escape_invisibles
    assert esc("BBC​ 1") == "BBC\\u200b 1"      # zero-width space escaped
    assert esc("UK ▎BBC") == "UK \\u258e" + "BBC" or esc("UK ▎BBC") == "UK \\u258eBBC"
    assert esc("plain name") == "plain name"          # printable text untouched


def test_action_registered_with_label(plugin_module):
    p = plugin_module.Plugin()
    entry = next(a for a in p.actions if a.get("id") == "test_regex_rules")
    assert entry.get("label")  # label-less actions are silently dropped by Dispatcharr


def _fake_streams(plugin_module, monkeypatch, names):
    monkeypatch.setattr(plugin_module.Plugin, "_get_all_streams",
                        lambda self, logger: [{"name": n, "id": i}
                                              for i, n in enumerate(names)])


def test_action_reports_counts_and_samples(plugin_module, monkeypatch):
    import logging
    _fake_streams(plugin_module, monkeypatch,
                  ["UK ▎BBC 1", "UK ▎ITV", "CNN"] + [f"S{i}" for i in range(30)])
    p = _plugin(plugin_module)
    settings = {"stream_name_regex_rules": json.dumps([["\\s*▎\\s*", " "]])}
    out = p.test_regex_rules_action(settings, logging.getLogger("t"))
    assert out["status"] == "success"
    assert "2 of 33" in out["message"]
    assert "\\u258e" in out["message"].lower()        # escaped rendering in samples
    assert "→" in out["message"]
    assert "normalization" in out["message"].lower()  # the after-this note
    assert "all" in out["message"].lower()            # unscoped disclaimer


def test_action_zero_streams_distinct_message(plugin_module, monkeypatch):
    import logging
    _fake_streams(plugin_module, monkeypatch, [])
    p = _plugin(plugin_module)
    out = p.test_regex_rules_action(
        {"stream_name_regex_rules": json.dumps([["a", "b"]])}, logging.getLogger("t"))
    assert out["status"] == "error" and "no streams" in out["message"].lower()


def test_action_all_rules_rejected(plugin_module, monkeypatch):
    import logging
    _fake_streams(plugin_module, monkeypatch, ["X"])
    p = _plugin(plugin_module)
    out = p.test_regex_rules_action(
        {"stream_name_regex_rules": json.dumps([["(a+)+$", ""]])}, logging.getLogger("t"))
    assert out["status"] == "error" and "unsafe_pattern" in out["message"]


def test_action_empty_setting(plugin_module, monkeypatch):
    import logging
    _fake_streams(plugin_module, monkeypatch, ["X"])
    p = _plugin(plugin_module)
    out = p.test_regex_rules_action({"stream_name_regex_rules": ""}, logging.getLogger("t"))
    assert out["status"] == "error" and "no rules" in out["message"].lower()


def test_action_renders_emptied_names(plugin_module, monkeypatch):
    import logging
    _fake_streams(plugin_module, monkeypatch, ["UK BBC"])
    p = _plugin(plugin_module)
    out = p.test_regex_rules_action(
        {"stream_name_regex_rules": json.dumps([["^UK.*", ""]])}, logging.getLogger("t"))
    assert "unmatchable" in out["message"]
