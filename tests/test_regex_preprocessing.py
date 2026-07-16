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
