"""Tests for the m3u_refresh auto-match feature (opt-in event-driven Match & Assign)."""
import logging

log = logging.getLogger("t")


def test_get_bool_setting_coerces_strings(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._get_bool_setting({"k": True}, "k") is True
    assert p._get_bool_setting({"k": "true"}, "k") is True
    assert p._get_bool_setting({"k": "TRUE"}, "k") is True
    assert p._get_bool_setting({"k": "yes"}, "k") is True
    assert p._get_bool_setting({"k": "1"}, "k") is True
    assert p._get_bool_setting({"k": "false"}, "k") is False
    assert p._get_bool_setting({"k": False}, "k") is False
    assert p._get_bool_setting({"k": "0"}, "k") is False
    assert p._get_bool_setting({}, "k", default=True) is True
    assert p._get_bool_setting({}, "k") is False


def test_should_auto_match_gate(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    ok = {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}
    assert p._should_auto_match_on_refresh(ok) is True
    assert p._should_auto_match_on_refresh({**ok, "auto_match_on_m3u_refresh": "true"}) is True
    assert p._should_auto_match_on_refresh({**ok, "auto_match_on_m3u_refresh": False}) is False
    assert p._should_auto_match_on_refresh({**ok, "auto_match_on_m3u_refresh": "false"}) is False
    assert p._should_auto_match_on_refresh({**ok, "profile_name": ""}) is False
    assert p._should_auto_match_on_refresh({"auto_match_on_m3u_refresh": True}) is False
    assert p._should_auto_match_on_refresh({**ok, "profile_name": "_none"}) is False
