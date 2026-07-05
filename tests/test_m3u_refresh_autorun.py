"""Tests for the m3u_refresh auto-match feature (opt-in event-driven Match & Assign)."""
import logging
import pytest

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


@pytest.fixture
def tmp_m3u(plugin_module, tmp_path, monkeypatch):
    """Redirect all lock/marker paths this feature touches onto tmp_path."""
    monkeypatch.setattr(plugin_module.PluginConfig, "M3U_REFRESH_LOCK_FILE",
                        str(tmp_path / "m3u_refresh.lock"))
    monkeypatch.setattr(plugin_module.PluginConfig, "M3U_REFRESH_PENDING_FILE",
                        str(tmp_path / "m3u_refresh_pending"))
    monkeypatch.setattr(plugin_module.PluginConfig, "OPERATION_LOCK_FILE",
                        str(tmp_path / "operation.lock"))
    return tmp_path


def test_m3u_refresh_pending_roundtrip(plugin_module, tmp_m3u):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._m3u_refresh_pending_set(log) is False
    p._set_m3u_refresh_pending(log)
    assert p._m3u_refresh_pending_set(log) is True
    p._clear_m3u_refresh_pending(log)
    assert p._m3u_refresh_pending_set(log) is False
    p._clear_m3u_refresh_pending(log)   # idempotent — no crash when already absent
    assert p._m3u_refresh_pending_set(log) is False
