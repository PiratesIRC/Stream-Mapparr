"""Dispatcharr 0.34 task banner for the long actions (requested 2026-11-22).

On 0.34 every plugin action runs in a Celery task. An action marked "async"
returns at once with a task id, the page shows a blue "<action> started" banner,
and Dispatcharr turns it green or red with the result message when the task
finishes (WebSocket plugin_task_complete). Stream-Mapparr used to start its own
daemon thread and return "started in background" within a second, so the task
ended immediately and the banner never carried the real result. Inside a task
the long actions now run inline, and progress goes to context["report_progress"].
"""
import json
import sys
import types
from pathlib import Path

import pytest

LONG_ACTIONS = {"preview_changes", "add_streams_to_channels", "sort_streams",
                "fill_mirror_backups", "match_us_ota_only", "probe_throughput",
                "manage_channel_visibility"}


def _manifest(plugin_module):
    path = Path(plugin_module.__file__).parent / "plugin.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def usage(plugin_module, monkeypatch):
    fake = types.SimpleNamespace(report=lambda *a, **k: None)
    monkeypatch.setattr(plugin_module, "USAGE", fake, raising=False)
    return fake


@pytest.fixture
def plugin(plugin_module, monkeypatch, usage):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None
    p._alias_map = None
    p.saved_settings = {}
    monkeypatch.setattr(p, "_initialize_fuzzy_matcher", lambda *a, **k: None)
    monkeypatch.setattr(p, "_resolve_match_threshold", lambda *a, **k: 85)
    monkeypatch.setattr(p, "_build_alias_map", lambda *a, **k: {})
    monkeypatch.setattr(p, "_check_operation_lock", lambda logger: (False, None))
    monkeypatch.setattr(p, "_send_progress_update", lambda *a, **k: None)
    return p


@pytest.fixture
def in_celery_task(monkeypatch):
    fake = types.ModuleType("celery")
    fake.current_task = types.SimpleNamespace(request=types.SimpleNamespace(id="task-1"))
    monkeypatch.setitem(sys.modules, "celery", fake)
    return fake


# --------------------------------------------------------------------------- #
# The manifest marks exactly the long actions async
# --------------------------------------------------------------------------- #

def test_only_the_long_actions_are_async(plugin_module):
    served = {a["id"] for a in plugin_module.Plugin.actions if a.get("async")}
    manifest = {a["id"] for a in _manifest(plugin_module)["actions"] if a.get("async")}
    assert served == LONG_ACTIONS
    assert manifest == LONG_ACTIONS


def test_the_event_handlers_are_not_async(plugin_module):
    for a in _manifest(plugin_module)["actions"]:
        if a["id"] in ("on_m3u_refresh", "on_iptv_checker_scan"):
            assert not a.get("async")


# --------------------------------------------------------------------------- #
# Detecting a Celery task
# --------------------------------------------------------------------------- #

def test_not_in_a_task_without_celery(plugin_module, monkeypatch):
    monkeypatch.setitem(sys.modules, "celery", None)   # import raises
    assert plugin_module.Plugin._in_celery_task() is False


def test_not_in_a_task_when_no_task_is_current(plugin_module, monkeypatch):
    fake = types.ModuleType("celery")
    fake.current_task = None
    monkeypatch.setitem(sys.modules, "celery", fake)
    assert plugin_module.Plugin._in_celery_task() is False


def test_in_a_task_when_a_task_is_current(plugin_module, in_celery_task):
    assert plugin_module.Plugin._in_celery_task() is True


# --------------------------------------------------------------------------- #
# Inside a task the long action runs inline and returns its real result
# --------------------------------------------------------------------------- #

def test_inside_a_task_the_action_runs_inline(plugin, plugin_module, in_celery_task, monkeypatch):
    lock = []
    monkeypatch.setattr(plugin, "_acquire_operation_lock", lambda action, logger: lock.append("on") or True)
    monkeypatch.setattr(plugin, "_release_operation_lock", lambda logger: lock.append("off"))
    monkeypatch.setattr(plugin, "_estimate_eta_seconds",
                        lambda *a: pytest.fail("no estimate is needed inside a task"))

    def no_thread(*a, **k):
        pytest.fail("a daemon thread was started inside a Celery task")

    monkeypatch.setattr(plugin_module, "threading", types.SimpleNamespace(Thread=no_thread))
    real = {"status": "success", "message": "Dry run complete. Would update 100 channels"}
    monkeypatch.setattr(plugin, "add_streams_to_channels_action", lambda *a, **k: real)
    assert plugin.run("add_streams_to_channels", {}, {}) == real
    assert lock == ["on", "off"]


def test_inside_a_task_an_error_result_reaches_dispatcharr(plugin, in_celery_task, monkeypatch):
    monkeypatch.setattr(plugin, "_acquire_operation_lock", lambda action, logger: True)
    monkeypatch.setattr(plugin, "_release_operation_lock", lambda logger: None)

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(plugin, "sort_streams_action", boom)
    result = plugin.run("sort_streams", {}, {})
    assert result["status"] == "error"
    assert "kaboom" in result["message"]


def test_outside_a_task_the_background_thread_path_is_kept(plugin, plugin_module, monkeypatch):
    monkeypatch.setattr(type(plugin), "_in_celery_task", staticmethod(lambda: False))
    monkeypatch.setattr(plugin, "_estimate_eta_seconds", lambda settings, logger: None)
    monkeypatch.setattr(plugin, "_acquire_operation_lock", lambda action, logger: True)
    monkeypatch.setattr(plugin, "_release_operation_lock", lambda logger: None)
    started = []

    class RecordThread:
        def __init__(self, target=None, name=None, daemon=None):
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(plugin_module, "threading", types.SimpleNamespace(Thread=RecordThread))
    monkeypatch.setattr(plugin, "add_streams_to_channels_action", lambda *a, **k: {"status": "success"})
    result = plugin.run("add_streams_to_channels", {}, {})
    assert result["background"] is True
    assert started == ["stream-mapparr-add_streams_to_channels"]


# --------------------------------------------------------------------------- #
# Progress goes to Dispatcharr's task progress reporter
# --------------------------------------------------------------------------- #

def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_progress_is_passed_to_report_progress(plugin_module):
    seen = []
    ctx = {"report_progress": lambda pct, msg: seen.append((pct, msg))}
    _bare(plugin_module)._report_task_progress(ctx, "add_streams_to_channels", 42.7, "Matching")
    assert seen == [(42, "Matching")]


def test_progress_is_clamped_and_internal_steps_are_skipped(plugin_module):
    seen = []
    ctx = {"report_progress": lambda pct, msg: seen.append(pct)}
    p = _bare(plugin_module)
    p._report_task_progress(ctx, "sort_streams", 140, "x")
    p._report_task_progress(ctx, "sort_streams", -5, "x")
    p._report_task_progress(ctx, "load_process_channels", 50, "x")
    assert seen == [100, 0]


def test_progress_without_a_reporter_or_with_a_failing_one_is_harmless(plugin_module):
    p = _bare(plugin_module)
    p._report_task_progress(None, "sort_streams", 10, "x")
    p._report_task_progress({}, "sort_streams", 10, "x")

    def broken(pct, msg):
        raise RuntimeError("redis down")

    p._report_task_progress({"report_progress": broken}, "sort_streams", 10, "x")


def test_send_progress_update_calls_the_reporter(plugin_module, monkeypatch):
    p = _bare(plugin_module)
    monkeypatch.setattr(p, "_persist_progress", lambda *a: {})
    seen = []
    ctx = {"report_progress": lambda pct, msg: seen.append((pct, msg))}
    p._send_progress_update("sort_streams", "running", 30, "Sorting", ctx)
    assert seen == [(30, "Sorting")]
