"""Stream-Mapparr reports its Streams Matched total through the vendored usage client.

Report sites: run() when an action's handler finishes (immediate, run-in-request and
background paths, whatever the result), _run_scheduled_sequence (scheduler and the IPTV
Checker hand-off), on_m3u_refresh_action, and _record_streams_matched after the tally file
is closed (always forced). The checkbox sits in its own section at the end of the form.
"""
import json
import types
from unittest.mock import MagicMock

import pytest


class FakeUsage:
    def __init__(self, on_report=None):
        self.calls = []
        self.on_report = on_report

    def report(self, settings=None, logger=None, force=False):
        if self.on_report:
            self.on_report()
        self.calls.append((settings, force))


@pytest.fixture(autouse=True)
def _no_real_reporting(plugin_module, monkeypatch, tmp_path):
    """No test in this file may reach /data/plugin_stats or the Worker."""
    real = getattr(plugin_module, "USAGE", None)
    started = []
    if real is not None:
        monkeypatch.setattr(real, "directory", str(tmp_path / "plugin_stats"), raising=False)
        # report() swallows every exception, so record a send attempt instead of raising.
        monkeypatch.setattr(real, "_start", lambda *a, **k: started.append(a), raising=False)
    yield
    assert started == [], "a test started a real usage send"


@pytest.fixture
def usage(plugin_module, monkeypatch):
    fake = FakeUsage()
    monkeypatch.setattr(plugin_module, "USAGE", fake, raising=False)
    return fake


@pytest.fixture
def plugin(plugin_module, monkeypatch):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None          # Plugin.__init__ sets this; run() reads it
    p._alias_map = None
    p.saved_settings = {}
    monkeypatch.setattr(p, "_initialize_fuzzy_matcher", lambda *a, **k: None)
    monkeypatch.setattr(p, "_resolve_match_threshold", lambda *a, **k: 85)
    monkeypatch.setattr(p, "_build_alias_map", lambda *a, **k: {})
    return p


class SyncThread:
    """Runs the target inside start(), so a test sees the background work finish."""
    def __init__(self, target=None, name=None, daemon=None, args=(), kwargs=None):
        self._target, self._args, self._kwargs = target, args, kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def test_the_reporter_is_configured_for_stream_mapparr(plugin_module):
    u = plugin_module.USAGE
    assert (u.plugin, u.counter, u.label) == ("stream-mapparr", "streams_matched", "Streams Matched")


def test_the_total_sums_the_match_tally(plugin_module, tmp_path, monkeypatch):
    ledger = tmp_path / "counts.jsonl"
    ledger.write_text(json.dumps({"streams": 5}) + "\n" + json.dumps({"streams": 7}) + "\n", encoding="utf-8")
    monkeypatch.setattr(plugin_module.PluginConfig, "MATCH_TALLY_FILE", str(ledger))
    assert plugin_module.USAGE.total_fn() == 12


def test_settings_are_read_under_the_install_key(plugin_module, monkeypatch):
    seen = []
    monkeypatch.setattr(plugin_module, "load_plugin_settings", lambda key, logger=None: seen.append(key) or {}, raising=False)
    plugin_module.USAGE.settings_fn()
    assert seen == [plugin_module.PluginConfig.PLUGIN_DB_KEY]


def test_a_written_tally_line_reports_forced_after_the_file_is_closed(plugin, plugin_module, tmp_path, monkeypatch):
    ledger = tmp_path / "t.jsonl"
    seen_lines = []
    fake = FakeUsage(on_report=lambda: seen_lines.append(ledger.read_text(encoding="utf-8").count("\n")))
    monkeypatch.setattr(plugin_module, "USAGE", fake, raising=False)
    monkeypatch.setattr(plugin_module.PluginConfig, "MATCH_TALLY_FILE", str(ledger))
    plugin._record_streams_matched("add_streams_to_channels", 2, 9, False)
    assert fake.calls == [(None, True)]
    assert seen_lines == [1]


@pytest.mark.parametrize("streams,dry_run", [(0, False), (9, True)])
def test_no_tally_line_means_no_report(plugin, plugin_module, usage, tmp_path, monkeypatch, streams, dry_run):
    monkeypatch.setattr(plugin_module.PluginConfig, "MATCH_TALLY_FILE", str(tmp_path / "t.jsonl"))
    plugin._record_streams_matched("add_streams_to_channels", 2, streams, dry_run)
    assert usage.calls == []


def test_a_failed_tally_write_does_not_report(plugin, plugin_module, usage, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "MATCH_TALLY_FILE", str(tmp_path / "no-dir" / "t.jsonl"))
    plugin._record_streams_matched("add_streams_to_channels", 2, 9, False)
    assert usage.calls == []


def test_a_reporter_that_raises_never_breaks_the_tally(plugin, plugin_module, tmp_path, monkeypatch):
    class Exploding:
        def report(self, settings=None, logger=None, force=False):
            raise RuntimeError("boom")

    monkeypatch.setattr(plugin_module, "USAGE", Exploding(), raising=False)
    ledger = tmp_path / "t.jsonl"
    monkeypatch.setattr(plugin_module.PluginConfig, "MATCH_TALLY_FILE", str(ledger))
    plugin._record_streams_matched("add_streams_to_channels", 2, 9, False)
    assert ledger.read_text(encoding="utf-8").count("\n") == 1


def test_the_form_ends_with_the_usage_section_and_checkbox(plugin, plugin_module):
    fields = plugin.fields
    assert fields[-1] == plugin_module.with_usage_field([], plugin_module.USAGE)[0]
    assert fields[-2]["id"] == "_section_usage" and fields[-2]["type"] == "info"
    assert [f.get("id") for f in fields].count("share_usage_counts") == 1


def test_an_immediate_action_reports_with_the_live_settings(plugin, usage, monkeypatch):
    monkeypatch.setattr(plugin, "view_last_results_action", lambda s, l: {"status": "success", "message": "ok"})
    settings = {"x": 1}
    result = plugin.run("view_last_results", settings, None)
    assert result == {"status": "success", "message": "ok"}
    assert usage.calls == [(settings, False)]


def test_an_immediate_action_that_returns_an_error_still_reports(plugin, usage, monkeypatch):
    error = {"status": "error", "message": "no"}
    monkeypatch.setattr(plugin, "view_last_results_action", lambda s, l: error)
    settings = {"x": 1}
    assert plugin.run("view_last_results", settings, None) == error
    assert usage.calls == [(settings, False)]


def test_an_immediate_action_that_raises_still_reports(plugin, usage, monkeypatch):
    def boom(s, l):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(plugin, "view_last_results_action", boom)
    settings = {"x": 1}
    result = plugin.run("view_last_results", settings, None)
    assert result == {"status": "error", "message": "kaboom"}
    assert usage.calls == [(settings, False)]


def _stub_run_in_request(plugin, monkeypatch, handler):
    monkeypatch.setattr(plugin, "_check_operation_lock", lambda logger: (False, None))
    monkeypatch.setattr(plugin, "_estimate_eta_seconds", lambda settings, logger: 1.0)
    monkeypatch.setattr(plugin, "_should_run_sync", lambda action, eta, lockable: True)
    monkeypatch.setattr(plugin, "_acquire_operation_lock", lambda action, logger: True)
    monkeypatch.setattr(plugin, "_release_operation_lock", lambda logger: None)
    monkeypatch.setattr(plugin, "_send_progress_update", lambda *a, **k: None)
    monkeypatch.setattr(plugin, "add_streams_to_channels_action", handler)


def test_a_run_in_request_action_reports_once(plugin, usage, monkeypatch):
    _stub_run_in_request(plugin, monkeypatch, lambda *a, **k: {"status": "success", "message": "ok"})
    settings = {"x": 1}
    result = plugin.run("add_streams_to_channels", settings, None)
    assert result == {"status": "success", "message": "ok"}
    assert usage.calls == [(settings, False)]


def _stub_background(plugin, plugin_module, monkeypatch, handler):
    _stub_run_in_request(plugin, monkeypatch, handler)
    monkeypatch.setattr(plugin, "_should_run_sync", lambda action, eta, lockable: False)
    monkeypatch.setattr(plugin, "_estimate_eta_seconds", lambda settings, logger: None)
    # Never patch the stdlib threading module itself: the usage client also uses it.
    monkeypatch.setattr(plugin_module, "threading", types.SimpleNamespace(Thread=SyncThread))


def test_a_background_action_reports_when_its_thread_finishes(plugin, plugin_module, usage, monkeypatch):
    _stub_background(plugin, plugin_module, monkeypatch, lambda *a, **k: {"status": "success", "message": "ok"})
    settings = {"x": 1}
    result = plugin.run("add_streams_to_channels", settings, None)
    assert result["background"] is True
    assert usage.calls == [(settings, False)]


def test_a_background_action_reports_even_when_its_handler_raises(plugin, plugin_module, usage, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaboom")

    _stub_background(plugin, plugin_module, monkeypatch, boom)
    settings = {"x": 1}
    result = plugin.run("add_streams_to_channels", settings, None)
    assert result["background"] is True
    assert usage.calls == [(settings, False)]


def test_a_background_action_reports_even_when_the_operation_lock_is_refused(plugin, plugin_module, usage, monkeypatch):
    _stub_background(plugin, plugin_module, monkeypatch, lambda *a, **k: {"status": "success", "message": "ok"})
    monkeypatch.setattr(plugin, "_acquire_operation_lock", lambda action, logger: False)
    settings = {"x": 1}
    result = plugin.run("add_streams_to_channels", settings, None)
    assert result["background"] is True
    assert usage.calls == [(settings, False)]


SCHEDULED_CASES = ["success", "load_failure", "skipped", "raises"]


@pytest.mark.parametrize("case", SCHEDULED_CASES)
def test_a_scheduled_run_reports_once_on_every_path(plugin, plugin_module, usage, tmp_path, monkeypatch, case):
    fake_bridge = types.SimpleNamespace(SCHEDULED_RUN_FILE=str(tmp_path / "s.json"),
                                        write_scheduled_run_ts=lambda *a, **k: None)
    monkeypatch.setattr(plugin, "_notify_bridge", lambda: fake_bridge)
    monkeypatch.setattr(plugin, "_wait_for_iptv_checker_completion", lambda *a, **k: True)
    monkeypatch.setattr(plugin, "sort_streams_action", lambda *a, **k: {"status": "success", "message": "sorted"})
    monkeypatch.setattr(plugin, "add_streams_to_channels_action",
                        lambda *a, **k: {"status": "success", "message": "added"})
    settings = {}
    if case == "load_failure":
        load_return = {"status": "error", "message": "no channels"}
        monkeypatch.setattr(plugin, "load_process_channels_action", lambda *a, **k: load_return)
    elif case == "raises":
        def boom(*a, **k):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(plugin, "load_process_channels_action", boom)
    else:
        monkeypatch.setattr(plugin, "load_process_channels_action",
                            lambda *a, **k: {"status": "success", "message": "loaded"})
    if case == "skipped":
        settings = {"run_after_iptv_checker_scan": True}
        monkeypatch.setattr(plugin, "_iptv_checker_is_running", lambda logger: True)

    logger = MagicMock()
    if case == "raises":
        with pytest.raises(RuntimeError):
            plugin._run_scheduled_sequence(settings, logger)
    else:
        returned = plugin._run_scheduled_sequence(settings, logger)
        expected = {
            "success": {"status": "success"},
            "load_failure": {"status": "error", "message": "no channels"},
            "skipped": {"status": "skipped",
                        "message": "Left to the IPTV Checker trigger, because a scan is in progress"},
        }[case]
        assert returned == expected
    assert usage.calls == [(None, False)]


def test_an_m3u_refresh_run_reports_once(plugin, plugin_module, usage, monkeypatch):
    monkeypatch.setattr(plugin, "_should_auto_match_on_refresh", lambda settings: True)
    monkeypatch.setattr(plugin, "_acquire_m3u_refresh_flock", lambda logger: object())
    monkeypatch.setattr(plugin, "_release_m3u_refresh_flock", lambda fd, logger: None)
    monkeypatch.setattr(plugin, "_clear_m3u_refresh_pending", lambda logger: None)
    monkeypatch.setattr(plugin, "_m3u_refresh_pending_set", lambda logger=None: False)
    monkeypatch.setattr(plugin, "_check_operation_lock", lambda logger: (False, None))
    monkeypatch.setattr(plugin, "_acquire_operation_lock", lambda action, logger: True)
    monkeypatch.setattr(plugin, "_release_operation_lock", lambda logger: None)
    monkeypatch.setattr(plugin, "add_streams_to_channels_action",
                        lambda *a, **k: {"status": "success", "message": "added"})
    context = {"settings": {"x": 1}}
    result = plugin.on_m3u_refresh_action({"payload": {"account_name": "A"}}, MagicMock(), context)
    assert result == {"status": "success", "message": "added"}
    assert usage.calls == [(None, False)]


def test_a_disabled_m3u_refresh_does_not_report(plugin, plugin_module, usage, monkeypatch):
    monkeypatch.setattr(plugin, "_should_auto_match_on_refresh", lambda settings: False)
    result = plugin.on_m3u_refresh_action({"payload": {}}, MagicMock(), {"settings": {}})
    assert result is None
    assert usage.calls == []
