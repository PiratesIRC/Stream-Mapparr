"""Disabling, deleting or reloading the plugin stops the background scheduler.

Dispatcharr's PluginManager.stop_plugin() calls the plugin's stop(context) on
disable, delete and reload (Plugins.md, dev branch, read 2026-10-17). It falls
back to run("stop", ...) only when the plugin has an action with id "stop", and
it never calls on_unload: the name appears in no Dispatcharr source file
(measured in the container, 2026-10-17). Stream-Mapparr had neither stop() nor a
stop action, so the scheduler thread kept running after a disable.

stop_plugin() runs in the one uWSGI worker that handled the request. The other
worker never constructs a disabled plugin again (discover_plugins skips it), so
its scheduler thread is stopped from inside the loop instead: on the periodic
database re-read, a row that says the plugin is disabled ends the loop. A read
that fails keeps the thread running, so an outage cannot stop the schedule.
"""
import inspect
import sys
import time
import types


def _wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _arm(plugin_module, monkeypatch, enabled_answer):
    """Arm a scheduler whose database re-read happens on its first tick.

    Asserts nothing about the thread: with a zero re-read interval a thread told
    the plugin is disabled can exit before the caller looks, and an assertion
    here would skip the caller's cleanup and leave the parked scheduler state
    pointing at a dead thread for every later test.
    """
    config = plugin_module.PluginConfig
    monkeypatch.setattr(config, "SCHEDULER_DB_REFRESH_INTERVAL", 0)
    monkeypatch.setattr(config, "SCHEDULER_CHECK_INTERVAL", 0.05)
    Plugin = plugin_module.Plugin
    monkeypatch.setattr(Plugin, "_settings_from_db", lambda self: {}, raising=True)
    monkeypatch.setattr(Plugin, "_enabled_in_db", lambda self: enabled_answer, raising=True)
    plugin = Plugin()
    plugin._stop_background_scheduler()
    plugin._start_background_scheduler({"scheduled_times": "0330"})
    return plugin, plugin_module._scheduler_state().bg_thread


def test_stop_takes_the_context_dispatcharr_passes(plugin_module):
    """stop_plugin() calls stop_method(context) first."""
    params = list(inspect.signature(plugin_module.Plugin.stop).parameters)
    assert params[:2] == ["self", "context"], params


def test_stop_with_a_context_stops_the_scheduler_thread(plugin_module):
    plugin = plugin_module.Plugin()
    try:
        plugin._start_background_scheduler({"scheduled_times": "0330"})
        thread = plugin_module._scheduler_state().bg_thread
        assert thread is not None and thread.is_alive()
        plugin.stop({"settings": {}, "reason": "disable"})
        assert not thread.is_alive(), "stop() must end the scheduler thread"
        assert plugin_module._scheduler_state().bg_thread is None
    finally:
        plugin._stop_background_scheduler()


def test_stop_without_a_context_also_works(plugin_module):
    plugin = plugin_module.Plugin()
    plugin.stop()
    assert plugin_module._scheduler_state().bg_thread is None


def test_a_stopped_scheduler_rearms_with_the_same_schedule(plugin_module):
    """Re-enable constructs the Plugin again with the same settings; that must start a thread."""
    plugin = plugin_module.Plugin()
    settings = {"scheduled_times": "0330"}
    try:
        plugin._start_background_scheduler(settings)
        plugin.stop({})
        plugin._start_background_scheduler(settings)
        thread = plugin_module._scheduler_state().bg_thread
        assert thread is not None and thread.is_alive()
    finally:
        plugin._stop_background_scheduler()


def test_the_loop_exits_when_the_database_says_disabled(plugin_module, monkeypatch):
    plugin, thread = _arm(plugin_module, monkeypatch, enabled_answer=False)
    try:
        assert thread is not None, "the scheduler thread must start"
        assert _wait_until(lambda: not thread.is_alive()), \
            "a row saying disabled must end the scheduler loop"
    finally:
        plugin._stop_background_scheduler()


def test_the_loop_keeps_running_when_the_database_says_enabled(plugin_module, monkeypatch):
    plugin, thread = _arm(plugin_module, monkeypatch, enabled_answer=True)
    try:
        time.sleep(0.3)
        assert thread is not None and thread.is_alive()
    finally:
        plugin._stop_background_scheduler()


def test_the_loop_keeps_running_when_the_database_cannot_be_asked(plugin_module, monkeypatch):
    """None is a failed read, not an instruction: an outage must not stop the schedule."""
    plugin, thread = _arm(plugin_module, monkeypatch, enabled_answer=None)
    try:
        time.sleep(0.3)
        assert thread is not None and thread.is_alive()
    finally:
        plugin._stop_background_scheduler()


def test_enabled_in_db_reads_the_enabled_flag(plugin_module, monkeypatch):
    """True and False come from the row; a missing row or a failed query is None."""
    class _Query:
        def __init__(self, value):
            self._value = value

        def filter(self, **kwargs):
            return self

        def values_list(self, *args, **kwargs):
            return self

        def first(self):
            if isinstance(self._value, Exception):
                raise self._value
            return self._value

    class _Model:
        objects = None

    # conftest stubs no apps.plugins package, so supply the one model the read imports.
    package = types.ModuleType("apps.plugins")
    models = types.ModuleType("apps.plugins.models")
    models.PluginConfig = _Model
    package.models = models
    monkeypatch.setitem(sys.modules, "apps.plugins", package)
    monkeypatch.setitem(sys.modules, "apps.plugins.models", models)
    plugin = plugin_module.Plugin.__new__(plugin_module.Plugin)
    for value, expected in ((True, True), (False, False), (None, None),
                            (RuntimeError("db down"), None)):
        _Model.objects = _Query(value)
        assert plugin._enabled_in_db() is expected, (value, expected)
