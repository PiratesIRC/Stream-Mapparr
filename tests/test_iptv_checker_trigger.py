"""The post-scan trigger: iptv_checker calls on_iptv_checker_scan after a scheduled scan.

Dispatcharr's connect event bus refuses event names outside its fixed list, so
the sibling plugin calls PluginManager.run_action directly. This file pins the
receiving side: the gate, the lock-held skip, the shared scheduled sequence and
the CSV header wording.
"""
import logging
import types

log = logging.getLogger("t")

TRIGGER = "IPTV Checker scan"


def _plugin(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.version = "test"
    return p


# --------------------------------------------------------------------------- #
# Interface
# --------------------------------------------------------------------------- #
def test_the_setting_sits_inside_the_iptv_checker_section(plugin_module):
    ids = [f.get("id") for f in _plugin(plugin_module).fields]
    start = ids.index("_section_iptv_checker")
    end = ids.index("_section_scheduling")
    assert "run_after_iptv_checker_scan" in ids[start:end]


def test_the_setting_defaults_off(plugin_module):
    field = next(f for f in _plugin(plugin_module).fields
                 if f.get("id") == "run_after_iptv_checker_scan")
    assert field["type"] == "boolean"
    assert field["default"] is False


def test_the_handler_action_is_served_without_a_button_or_an_event(plugin_module):
    action = next(a for a in _plugin(plugin_module).actions
                  if a["id"] == "on_iptv_checker_scan")
    assert action.get("label")
    assert "button_color" not in action
    assert "events" not in action


def test_the_gate_reads_the_setting(plugin_module):
    p = _plugin(plugin_module)
    assert p._should_run_after_iptv_checker_scan({"run_after_iptv_checker_scan": True}) is True
    assert p._should_run_after_iptv_checker_scan({"run_after_iptv_checker_scan": "true"}) is True
    assert p._should_run_after_iptv_checker_scan({"run_after_iptv_checker_scan": False}) is False
    assert p._should_run_after_iptv_checker_scan({}) is False


# --------------------------------------------------------------------------- #
# The handler
# --------------------------------------------------------------------------- #
def _wire(monkeypatch, p, locked=False):
    calls = []
    monkeypatch.setattr(p, "_check_operation_lock", lambda logger: (locked, {}))
    monkeypatch.setattr(p, "_acquire_operation_lock", lambda name, logger: True)
    monkeypatch.setattr(p, "_release_operation_lock", lambda logger: calls.append("released"))
    monkeypatch.setattr(
        p, "_run_scheduled_sequence",
        lambda settings, logger, trigger=None:
        calls.append(("run", settings, trigger)) or {"status": "success"})
    return calls


def test_the_handler_does_nothing_when_the_setting_is_off(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls = _wire(monkeypatch, p)
    ctx = {"settings": {"run_after_iptv_checker_scan": False}}
    assert p.on_iptv_checker_scan_action({"event": "x", "payload": {}}, log, ctx) is None
    assert calls == []


def test_the_handler_reads_settings_from_the_context_not_the_params(plugin_module, monkeypatch):
    """run_action passes the event dict as the 2nd argument, the real settings in context."""
    p = _plugin(plugin_module)
    calls = _wire(monkeypatch, p)
    real = {"run_after_iptv_checker_scan": True, "scheduled_sort_streams": True}
    p.on_iptv_checker_scan_action({"event": "x", "payload": {"streams_checked": 3}}, log,
                                  {"settings": real})
    assert calls == [("run", real, TRIGGER), "released"]


def test_the_handler_skips_when_another_operation_holds_the_lock(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls = _wire(monkeypatch, p, locked=True)
    ctx = {"settings": {"run_after_iptv_checker_scan": True}}
    assert p.on_iptv_checker_scan_action({}, log, ctx) is None
    assert calls == []


def test_the_handler_releases_the_lock_when_the_sequence_raises(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls = _wire(monkeypatch, p)

    def boom(settings, logger, trigger=None):
        raise RuntimeError("sequence failed")
    monkeypatch.setattr(p, "_run_scheduled_sequence", boom)
    ctx = {"settings": {"run_after_iptv_checker_scan": True}}
    result = p.on_iptv_checker_scan_action({}, log, ctx)
    assert result is None
    assert calls == ["released"]


def test_run_dispatches_the_handler_before_the_generic_path(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    seen = []
    monkeypatch.setattr(p, "on_iptv_checker_scan_action",
                        lambda s, l, c: seen.append((s, c)) or {"status": "success"})
    params = {"event": "iptv_checker_scan_complete", "payload": {}}
    ctx = {"settings": {"run_after_iptv_checker_scan": True}}
    assert p.run("on_iptv_checker_scan", params, ctx) == {"status": "success"}
    assert seen == [(params, ctx)]


# --------------------------------------------------------------------------- #
# The shared scheduled sequence
# --------------------------------------------------------------------------- #
def _wire_sequence(monkeypatch, p, load_ok=True):
    calls = []
    monkeypatch.setattr(p, "_wait_for_iptv_checker_completion",
                        lambda settings, logger: calls.append("wait") or True)
    monkeypatch.setattr(
        p, "load_process_channels_action",
        lambda settings, logger, context=None: calls.append("load") or
        {"status": "success" if load_ok else "error", "message": "m"})
    monkeypatch.setattr(
        p, "sort_streams_action",
        lambda settings, logger, context=None, is_scheduled=False, trigger=None:
        calls.append(("sort", is_scheduled, trigger)) or {"status": "success"})
    monkeypatch.setattr(
        p, "add_streams_to_channels_action",
        lambda settings, logger, is_scheduled=False, context=None, trigger=None:
        calls.append(("match", is_scheduled, trigger)) or {"status": "success"})
    stamps = []
    bridge = types.SimpleNamespace(SCHEDULED_RUN_FILE="/x",
                                   write_scheduled_run_ts=lambda path, ts: stamps.append(path))
    monkeypatch.setattr(p, "_notify_bridge", lambda: bridge)
    return calls, stamps


def test_the_sequence_honours_the_schedule_toggles(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, _ = _wire_sequence(monkeypatch, p)
    settings = {"scheduled_sort_streams": True, "scheduled_match_streams": False}
    result = p._run_scheduled_sequence(settings, log, trigger=TRIGGER)
    assert result["status"] == "success"
    assert calls == ["load", ("sort", True, TRIGGER)]


def test_the_sequence_runs_match_when_that_toggle_is_on(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, _ = _wire_sequence(monkeypatch, p)
    settings = {"scheduled_sort_streams": "false", "scheduled_match_streams": "true"}
    p._run_scheduled_sequence(settings, log)
    assert calls == ["wait", "load", ("match", True, None)]


def test_the_timer_path_waits_for_the_checker_and_the_triggered_path_does_not(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, _ = _wire_sequence(monkeypatch, p)
    p._run_scheduled_sequence({}, log)
    assert calls[0] == "wait"
    calls.clear()
    p._run_scheduled_sequence({}, log, trigger=TRIGGER)
    assert "wait" not in calls


def test_only_the_timer_path_writes_the_scheduled_run_timestamp(plugin_module, monkeypatch):
    """The timestamp means 'the timer fired'; a scan-triggered run must not fake it."""
    p = _plugin(plugin_module)
    _, stamps = _wire_sequence(monkeypatch, p)
    p._run_scheduled_sequence({"scheduled_sort_streams": True}, log)
    assert stamps == ["/x"]
    p._run_scheduled_sequence({"scheduled_sort_streams": True}, log, trigger=TRIGGER)
    assert stamps == ["/x"]


def test_the_sequence_stops_when_channels_fail_to_load(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, stamps = _wire_sequence(monkeypatch, p, load_ok=False)
    result = p._run_scheduled_sequence({"scheduled_sort_streams": True}, log)
    assert result["status"] == "error"
    assert calls == ["wait", "load"]
    assert stamps == []


# --------------------------------------------------------------------------- #
# The CSV header says who started the run
# --------------------------------------------------------------------------- #
def _mode_line(plugin_module, **kw):
    p = _plugin(plugin_module)
    header = p._generate_csv_header_comment({}, {}, action_name="Sort", **kw)
    return next(line for line in header.splitlines() if line.startswith("# Execution Mode:"))


def test_a_triggered_run_names_its_trigger_in_the_header(plugin_module):
    line = _mode_line(plugin_module, is_scheduled=True, trigger=TRIGGER)
    assert "Scheduled" in line and TRIGGER in line


def test_a_timer_run_does_not_mention_a_trigger(plugin_module):
    line = _mode_line(plugin_module, is_scheduled=True)
    assert "Scheduled" in line and "after" not in line


# --------------------------------------------------------------------------- #
# The timer run stands aside when the trigger will cover the day (2026-09-14)
# --------------------------------------------------------------------------- #
def _wire_running(monkeypatch, p, running):
    monkeypatch.setattr(p, "_iptv_checker_is_running", lambda logger: running)


def test_the_timer_skips_when_the_trigger_is_on_and_the_checker_is_running(plugin_module, monkeypatch):
    """Measured 2026-09-14: the waiting timer run and the triggered run sorted
    the same channels concurrently, one second apart, in two workers."""
    p = _plugin(plugin_module)
    calls, stamps = _wire_sequence(monkeypatch, p)
    _wire_running(monkeypatch, p, True)
    settings = {"run_after_iptv_checker_scan": True, "scheduled_sort_streams": True,
                "scheduled_match_streams": False}
    result = p._run_scheduled_sequence(settings, log)
    assert result["status"] == "skipped"
    assert calls == []
    assert stamps == []


def test_the_timer_still_waits_when_the_trigger_is_off(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, _ = _wire_sequence(monkeypatch, p)
    _wire_running(monkeypatch, p, True)
    settings = {"run_after_iptv_checker_scan": False, "scheduled_sort_streams": True,
                "scheduled_match_streams": False}
    p._run_scheduled_sequence(settings, log)
    assert calls == ["wait", "load", ("sort", True, None)]


def test_the_timer_runs_normally_when_the_checker_is_idle(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, _ = _wire_sequence(monkeypatch, p)
    _wire_running(monkeypatch, p, False)
    settings = {"run_after_iptv_checker_scan": True, "scheduled_sort_streams": True,
                "scheduled_match_streams": False}
    p._run_scheduled_sequence(settings, log)
    assert calls == ["wait", "load", ("sort", True, None)]


def test_the_triggered_path_never_consults_the_running_check(plugin_module, monkeypatch):
    p = _plugin(plugin_module)
    calls, _ = _wire_sequence(monkeypatch, p)

    def boom(logger):
        raise AssertionError("the triggered path must not ask whether the checker is running")
    monkeypatch.setattr(p, "_iptv_checker_is_running", boom)
    settings = {"run_after_iptv_checker_scan": True, "scheduled_sort_streams": True,
                "scheduled_match_streams": False}
    p._run_scheduled_sequence(settings, log, trigger=TRIGGER)
    assert calls == ["load", ("sort", True, TRIGGER)]


def _progress(plugin_module, monkeypatch, tmp_path, content):
    path = tmp_path / "iptv_checker_progress.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(plugin_module.PluginConfig, "IPTV_CHECKER_PROGRESS_FILE", str(path))


def test_running_check_reads_the_progress_file(plugin_module, monkeypatch, tmp_path):
    p = _plugin(plugin_module)
    _progress(plugin_module, monkeypatch, tmp_path, '{"status": "running", "current": 3, "total": 9}')
    assert p._iptv_checker_is_running(log) is True
    _progress(plugin_module, monkeypatch, tmp_path, '{"status": "idle"}')
    assert p._iptv_checker_is_running(log) is False


def test_running_check_fails_open_to_not_running(plugin_module, monkeypatch, tmp_path):
    """A missing or unreadable file must not make the timer stand aside, or a
    checker that is not installed would silence the schedule forever."""
    p = _plugin(plugin_module)
    _progress(plugin_module, monkeypatch, tmp_path, None)
    assert p._iptv_checker_is_running(log) is False
    _progress(plugin_module, monkeypatch, tmp_path, "{not json")
    assert p._iptv_checker_is_running(log) is False
