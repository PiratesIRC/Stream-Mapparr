"""Tests for the m3u_refresh auto-match feature (opt-in event-driven Match & Assign)."""
import logging
import types
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


def test_m3u_flock_degrades_without_fcntl(plugin_module, tmp_m3u, monkeypatch):
    """No fcntl (Windows/pytest) -> acquire returns a usable fd; release is a safe no-op."""
    monkeypatch.setattr(plugin_module, "fcntl", None)
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    fd = p._acquire_m3u_refresh_flock(log)
    assert fd is not None
    p._release_m3u_refresh_flock(fd, log)   # must not raise


def test_m3u_flock_loser_returns_none(plugin_module, tmp_m3u, monkeypatch):
    """A live holder -> a second (non-stale) acquire fails LOCK_NB and returns None."""
    def would_block(fileno, op):
        raise OSError("would block")
    monkeypatch.setattr(plugin_module, "fcntl",
                        types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=would_block))
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._acquire_m3u_refresh_flock(log) is None   # fresh lock file -> not stale -> None


from unittest.mock import MagicMock


def _enabled_instance(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._op_total_items = None
    p.add_streams_to_channels_action = MagicMock(return_value={"status": "success", "message": "ok"})
    return p


def test_on_m3u_refresh_action_registered(plugin_module):
    actions = {a["id"]: a for a in plugin_module.Plugin.actions}
    assert "on_m3u_refresh" in actions
    a = actions["on_m3u_refresh"]
    assert a.get("label")                       # required or _normalize_actions drops it
    assert a.get("events") == ["m3u_refresh"]
    assert "button_label" not in a              # hidden from the UI


def test_on_m3u_refresh_runs_when_enabled(plugin_module, tmp_m3u):
    p = _enabled_instance(plugin_module)
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}}
    params = {"event": "m3u_refresh", "payload": {"account_name": "Prov"}}
    result = p.run("on_m3u_refresh", params, ctx)
    assert p.add_streams_to_channels_action.call_count == 1
    # config MUST come from context["settings"], not the fieldless event params
    assert p.add_streams_to_channels_action.call_args.args[0] is ctx["settings"]
    assert result == {"status": "success", "message": "ok"}


def test_on_m3u_refresh_skips_when_disabled(plugin_module, tmp_m3u):
    p = _enabled_instance(plugin_module)
    ctx = {"settings": {"auto_match_on_m3u_refresh": False, "profile_name": "TV"}}
    result = p.run("on_m3u_refresh", {"event": "m3u_refresh", "payload": {}}, ctx)
    assert p.add_streams_to_channels_action.call_count == 0
    assert result is None


def test_on_m3u_refresh_skips_without_profile(plugin_module, tmp_m3u):
    p = _enabled_instance(plugin_module)
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": ""}}
    result = p.run("on_m3u_refresh", {"event": "m3u_refresh"}, ctx)
    assert p.add_streams_to_channels_action.call_count == 0
    assert result is None


def test_on_m3u_refresh_loser_sets_pending(plugin_module, tmp_m3u, monkeypatch):
    p = _enabled_instance(plugin_module)
    monkeypatch.setattr(p, "_acquire_m3u_refresh_flock", lambda logger: None)
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}}
    result = p.run("on_m3u_refresh", {"event": "m3u_refresh", "payload": {"account_name": "B"}}, ctx)
    assert p.add_streams_to_channels_action.call_count == 0
    assert p._m3u_refresh_pending_set(log) is True
    assert result is None


def test_on_m3u_refresh_reruns_once_then_stops(plugin_module, tmp_m3u):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._op_total_items = None
    calls = {"n": 0}

    def spy(settings, logger, context=None):
        calls["n"] += 1
        if calls["n"] == 1:
            p._set_m3u_refresh_pending(logger)   # simulate a late account arriving mid-run
        return {"status": "success"}

    p.add_streams_to_channels_action = spy
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}}
    p.run("on_m3u_refresh", {"event": "m3u_refresh", "payload": {"account_name": "A"}}, ctx)
    assert calls["n"] == 2                        # bounded: one rerun, not a livelock
    assert p._m3u_refresh_pending_set(log) is False


def test_on_m3u_refresh_skips_when_operation_locked(plugin_module, tmp_m3u, monkeypatch):
    p = _enabled_instance(plugin_module)
    monkeypatch.setattr(p, "_check_operation_lock",
                        lambda logger: (True, {"action": "manual", "age_minutes": 1.0}))
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}}
    p.run("on_m3u_refresh", {"event": "m3u_refresh"}, ctx)
    assert p.add_streams_to_channels_action.call_count == 0


def test_on_m3u_refresh_handles_missing_payload(plugin_module, tmp_m3u):
    p = _enabled_instance(plugin_module)
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}}
    p.run("on_m3u_refresh", {"event": "m3u_refresh"}, ctx)             # no payload key
    assert p.add_streams_to_channels_action.call_count == 1
    p.add_streams_to_channels_action.reset_mock()
    p.run("on_m3u_refresh", {"event": "m3u_refresh", "payload": {}}, ctx)  # payload w/o account_name
    assert p.add_streams_to_channels_action.call_count == 1


def test_on_m3u_refresh_releases_locks_on_exception(plugin_module, tmp_m3u):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._op_total_items = None

    def boom(settings, logger, context=None):
        raise RuntimeError("match failed")

    p.add_streams_to_channels_action = boom
    ctx = {"settings": {"auto_match_on_m3u_refresh": True, "profile_name": "TV"}}
    with pytest.raises(RuntimeError):
        p.on_m3u_refresh_action({"event": "m3u_refresh", "payload": {}}, log, ctx)
    assert p._check_operation_lock(log)[0] is False    # operation lock released
    fd = p._acquire_m3u_refresh_flock(log)             # flock released -> re-acquirable
    assert fd is not None
    p._release_m3u_refresh_flock(fd, log)
