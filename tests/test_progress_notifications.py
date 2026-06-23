"""Tests for the IPTV-Checker-style progress/results/notification feature.

Covers: format_eta, Discord/Slack webhook reshaping (issue #32), persisted
progress + last-results round-trips, and the View Check Progress / View Last
Results action message formatting. All run under the Django-stubbed conftest.
"""
import json
import logging
import time

import pytest

LOG = logging.getLogger("test")


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --------------------------------------------------------------------------- #
# format_eta (module-level helper)
# --------------------------------------------------------------------------- #
def test_format_eta_seconds(plugin_module):
    assert plugin_module.format_eta(45) == "45s"


def test_format_eta_minutes(plugin_module):
    assert plugin_module.format_eta(90) == "1m 30s"


def test_format_eta_hours(plugin_module):
    assert plugin_module.format_eta(3700) == "1h 1m"


def test_format_eta_clamps_negative_and_none(plugin_module):
    assert plugin_module.format_eta(-5) == "0s"
    assert plugin_module.format_eta(None) == "0s"


# --------------------------------------------------------------------------- #
# Discord/Slack webhook reshaping — issue #32
# --------------------------------------------------------------------------- #
PAYLOAD = {"plugin": "stream-mapparr", "event": "add_streams_to_channels.complete",
           "action": "add_streams_to_channels", "status": "success",
           "message": "Matched and assigned 132 streams across 23 channels.",
           "timestamp": "2026-06-23T12:00:00Z"}


def test_webhook_discord_uses_content_key(plugin_module):
    body = json.loads(plugin_module.Plugin._build_webhook_body(
        "https://discord.com/api/webhooks/123/abc", PAYLOAD))
    assert set(body.keys()) == {"content"}
    assert "132 streams" in body["content"]


def test_webhook_discordapp_host_also_supported(plugin_module):
    body = json.loads(plugin_module.Plugin._build_webhook_body(
        "https://discordapp.com/api/webhooks/123/abc", PAYLOAD))
    assert "content" in body


def test_webhook_slack_uses_text_key(plugin_module):
    body = json.loads(plugin_module.Plugin._build_webhook_body(
        "https://hooks.slack.com/services/T/B/X", PAYLOAD))
    assert set(body.keys()) == {"text"}
    assert "132 streams" in body["text"]


def test_webhook_generic_url_keeps_full_payload(plugin_module):
    body = json.loads(plugin_module.Plugin._build_webhook_body(
        "https://example.com/hook", PAYLOAD))
    assert body == PAYLOAD


def test_webhook_discord_content_truncated_to_2000(plugin_module):
    big = dict(PAYLOAD, message="x" * 5000)
    body = json.loads(plugin_module.Plugin._build_webhook_body(
        "https://discord.com/api/webhooks/1/2", big))
    assert len(body["content"]) == 2000


# --------------------------------------------------------------------------- #
# Persisted progress + last-results round-trip
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_state(plugin_module, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "PROGRESS_STATE_FILE",
                        str(tmp_path / "progress.json"))
    monkeypatch.setattr(plugin_module.PluginConfig, "LAST_RESULTS_FILE",
                        str(tmp_path / "last_results.json"))
    return tmp_path


def test_progress_state_round_trip(plugin_module, tmp_state):
    p = _bare(plugin_module)
    p._save_progress_state({"action": "sort_streams", "status": "running",
                            "progress": 50, "start_time": 100.0})
    loaded = p._load_progress_state()
    assert loaded["action"] == "sort_streams"
    assert loaded["status"] == "running"
    assert loaded["progress"] == 50


def test_progress_state_default_is_idle_when_missing(plugin_module, tmp_state):
    p = _bare(plugin_module)
    assert p._load_progress_state() == {"status": "idle"}


# --------------------------------------------------------------------------- #
# View Check Progress action
# --------------------------------------------------------------------------- #
def test_view_progress_running_shows_pct_and_eta(plugin_module, tmp_state):
    p = _bare(plugin_module)
    now = time.time()
    p._save_progress_state({"action": "add_streams_to_channels", "status": "running",
                            "progress": 40, "start_time": now - 60, "updated_at": now,
                            "message": "Processing..."})
    out = p.view_check_progress_action({}, LOG)
    assert out["status"] == "success"
    assert "40%" in out["message"]
    assert "ETA" in out["message"]


def test_view_progress_idle_when_nothing_running(plugin_module, tmp_state):
    p = _bare(plugin_module)
    out = p.view_check_progress_action({}, LOG)
    assert out["status"] == "success"
    assert "No operation" in out["message"]


# --------------------------------------------------------------------------- #
# View Last Results action
# --------------------------------------------------------------------------- #
def test_view_last_results_no_data(plugin_module, tmp_state):
    p = _bare(plugin_module)
    out = p.view_last_results_action({}, LOG)
    assert "No results" in out["message"]


def test_view_last_results_formats_summary(plugin_module, tmp_state):
    p = _bare(plugin_module)
    p._save_last_results("add_streams_to_channels", "success",
                         "Matched and assigned 132 streams across 23 channels.",
                         {"channels_updated": 23, "streams_assigned": 132}, duration_seconds=19)
    out = p.view_last_results_action({}, LOG)
    msg = out["message"]
    assert "Add Streams To Channels" in msg
    assert "132 streams" in msg
    assert "channels updated" in msg.lower()


def test_view_last_results_points_to_progress_when_running(plugin_module, tmp_state):
    p = _bare(plugin_module)
    p._save_progress_state({"action": "sort_streams", "status": "running", "progress": 10,
                            "start_time": time.time(), "updated_at": time.time()})
    out = p.view_last_results_action({}, LOG)
    assert "View Check Progress" in out["message"]


# --------------------------------------------------------------------------- #
# Live-notification wiring (integration)
# --------------------------------------------------------------------------- #
def test_progress_toast_interval_adaptive(plugin_module):
    f = plugin_module._progress_toast_interval_for
    assert f(30) == 3
    assert f(100) == 5
    assert f(500) == 10
    assert f(None) == 5


def test_persist_progress_new_op_emits_started_toast(plugin_module, tmp_state, monkeypatch):
    p = _bare(plugin_module)
    toasts = []
    monkeypatch.setattr(p, "_emit_plugin_toast", lambda m: toasts.append(m))
    p._persist_progress("sort_streams", "running", 0, "Starting...")
    assert any("started" in t.lower() for t in toasts)
    st = p._load_progress_state()
    assert st["status"] == "running" and st["action"] == "sort_streams"


def test_send_progress_update_completion_writes_last_results(plugin_module, tmp_state):
    p = _bare(plugin_module)
    p._send_progress_update("sort_streams", "success", 100, "Sorted 5 streams.", None, {"streams_sorted": 5})
    data = p._read_json_file(plugin_module.PluginConfig.LAST_RESULTS_FILE)
    assert data["message"] == "Sorted 5 streams."
    assert data["details"]["streams_sorted"] == 5
    assert data["status"] == "success"


def test_internal_load_action_is_invisible_to_progress(plugin_module, tmp_state, monkeypatch):
    """load_process_channels is an internal sub-step: it must not overwrite the
    parent op's progress state nor fire a started toast (avoids double-started)."""
    p = _bare(plugin_module)
    # Parent op is running.
    p._save_progress_state({"action": "add_streams_to_channels", "status": "running",
                            "progress": 30, "start_time": time.time(), "updated_at": time.time()})
    toasts = []
    monkeypatch.setattr(p, "_emit_plugin_toast", lambda m: toasts.append(m))
    p._persist_progress("load_process_channels", "running", 50, "Fetching streams...")
    assert toasts == []                       # no toast for internal step
    assert p._load_progress_state()["action"] == "add_streams_to_channels"  # parent unchanged


def test_internal_load_completion_does_not_overwrite_last_results(plugin_module, tmp_state):
    p = _bare(plugin_module)
    p._save_last_results("add_streams_to_channels", "success", "Real parent result", {"x": 1})
    p._send_progress_update("load_process_channels", "success", 100, "Loaded channels", None, {"y": 2})
    data = p._read_json_file(plugin_module.PluginConfig.LAST_RESULTS_FILE)
    assert data["message"] == "Real parent result"   # parent summary preserved


def test_persist_progress_returns_state_for_reuse(plugin_module, tmp_state):
    """QA M1: _persist_progress returns the state it wrote so the completion path
    can reuse it instead of a second disk read."""
    p = _bare(plugin_module)
    st = p._persist_progress("sort_streams", "success", 100, "done")
    assert st["status"] == "complete" and "end_time" in st
    assert p._persist_progress("load_process_channels", "running", 5, "x") is None  # internal -> None


def test_view_last_results_shown_despite_stale_running_flag(plugin_module, tmp_state):
    """QA M3: a crashed run leaves status='running' forever; View Last Results must
    still surface the last good result once the flag is stale (>180s)."""
    p = _bare(plugin_module)
    p._save_last_results("add_streams_to_channels", "success", "Assigned 99 streams.", {"streams_assigned": 99})
    p._save_progress_state({"action": "add_streams_to_channels", "status": "running",
                            "progress": 60, "start_time": time.time() - 9000,
                            "updated_at": time.time() - 9000})  # stale
    out = p.view_last_results_action({}, LOG)
    assert "99 streams" in out["message"]


def test_started_toast_has_no_item_count(plugin_module, tmp_state, monkeypatch):
    """QA M2: started toast must not claim an item count (unknown at start)."""
    p = _bare(plugin_module)
    p._op_total_items = 5000  # stale from a previous op
    toasts = []
    monkeypatch.setattr(p, "_emit_plugin_toast", lambda m: toasts.append(m))
    p._persist_progress("manage_channel_visibility", "running", 0, "start")
    assert toasts and "5000" not in toasts[0] and "started" in toasts[0].lower()
