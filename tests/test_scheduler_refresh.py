"""The scheduler loop re-reads its schedule instead of keeping the one it started with.

Measured on the live installation on 2026-09-05: the daily job ran TWICE, once at
05:00 and again at 05:05, although the saved schedule held a single time, 0505.
The cross-worker slot claim file recorded both slots as claimed for that date and
two report files were written five minutes apart.

The cause is that scheduler_loop closes over the parsed time list and the settings
dictionary it was handed when the thread was armed, and never looks at either
again. Re-arming happens only inside Plugin.__init__, and Dispatcharr 0.30.0
constructs the Plugin only inside PluginManager.discover_plugins, which is cached
per process and re-runs only when the plugin reload token file is newer or a
caller forces a reload. A uWSGI worker reaches that path through the plugins API.
A Celery worker discovers once, at worker_ready, and then never again. So a worker
process kept firing the schedule it was armed with when the container started,
three days after the operator had changed it, and only a container restart would
have cleared it.

Consulting the database once per process at construction, which is what
_reconcile_schedule_with_db does, cannot fix this: that check is deliberately
performed at most once per process and so cannot see a change made later in the
same process's life.

The database is the authority here for the same reason it is in
_reconcile_schedule_with_db: the settings file is a cache of what the interface
last wrote, and it drifts.
"""
import ast
import os
from datetime import time as dtime

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Stream-Mapparr", "plugin.py")


def _bare(plugin_module):
    """A Plugin with no __init__ run, so nothing touches the ORM or the disk."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def _plugin_with_db(plugin_module, monkeypatch, db_settings):
    """A Plugin whose database read returns exactly `db_settings`."""
    plugin = _bare(plugin_module)
    monkeypatch.setattr(
        type(plugin), "_settings_from_db", lambda self: db_settings, raising=True)
    return plugin


# --------------------------------------------------------------------------- #
# Adopting a change
# --------------------------------------------------------------------------- #
def test_a_schedule_changed_in_the_database_is_adopted(plugin_module, monkeypatch):
    """The exact live failure: the loop is armed for 05:00, the row says 05:05."""
    plugin = _plugin_with_db(
        plugin_module, monkeypatch, {"scheduled_times": "0505", "profile_name": "a"})

    settings, times = plugin._refresh_schedule_from_db(
        {"scheduled_times": "0500", "profile_name": "a"}, [dtime(5, 0)])

    assert times == [dtime(5, 5)]
    assert settings["scheduled_times"] == "0505"


def test_a_schedule_the_operator_cleared_is_adopted_as_no_times(plugin_module, monkeypatch):
    """Clearing the schedule must stop the loop firing, not leave the old time armed.

    This is the opposite direction of the same drift and it has to work, or a
    schedule the operator deliberately removed keeps running until a restart.
    """
    plugin = _plugin_with_db(
        plugin_module, monkeypatch, {"scheduled_times": "", "profile_name": "a"})

    _settings, times = plugin._refresh_schedule_from_db(
        {"scheduled_times": "0500", "profile_name": "a"}, [dtime(5, 0)])

    assert times == []


def test_settings_that_changed_without_the_times_changing_are_adopted(plugin_module, monkeypatch):
    """The loop passes its settings to the actions, so those drift too.

    A worker armed before the operator narrowed the channel groups would keep
    running the scheduled job against the old group list. Same staleness, same
    cause, and it is invisible because the run still succeeds.
    """
    plugin = _plugin_with_db(plugin_module, monkeypatch, {
        "scheduled_times": "0500", "selected_groups": "US: News"})

    settings, times = plugin._refresh_schedule_from_db(
        {"scheduled_times": "0500", "selected_groups": "US: News, US: Sports"},
        [dtime(5, 0)])

    assert settings["selected_groups"] == "US: News"
    assert times == [dtime(5, 0)]


# --------------------------------------------------------------------------- #
# Overlaying, not replacing
# --------------------------------------------------------------------------- #
# Measured on the live installation on 2026-09-05, after the first version of this
# refresh had been deployed: the settings FILE the scheduler arms from held 23 keys
# that the plugin's database row does not carry at all, 17 of them with real values
# including tag_handling, visible_channel_limit, rate_limiting, filter_dead_streams
# and allow_same_name_streams. No key existed in both and disagreed; the file is a
# strict superset. Replacing the loop's settings with the database row therefore
# DROPPED those 17 values, and the scheduled run would have fallen back to code
# defaults for every one of them. The database has to be overlaid onto what the loop
# already holds, not substituted for it.
def test_settings_the_database_row_does_not_carry_are_kept(plugin_module, monkeypatch):
    """The live shape: the row is a strict subset of the file the loop armed from."""
    plugin = _plugin_with_db(plugin_module, monkeypatch, {"scheduled_times": "0505"})

    settings, times = plugin._refresh_schedule_from_db(
        {"scheduled_times": "0500", "tag_handling": "keep_regional",
         "visible_channel_limit": 3, "allow_same_name_streams": True},
        [dtime(5, 0)])

    assert times == [dtime(5, 5)]
    assert settings["tag_handling"] == "keep_regional"
    assert settings["visible_channel_limit"] == 3
    assert settings["allow_same_name_streams"] is True


def test_a_value_the_database_carries_wins_over_the_one_in_hand(plugin_module, monkeypatch):
    """Overlay direction: where the row HAS an opinion, the row is the authority."""
    plugin = _plugin_with_db(
        plugin_module, monkeypatch, {"scheduled_times": "0500", "tag_handling": "strip_all"})

    settings, _times = plugin._refresh_schedule_from_db(
        {"scheduled_times": "0500", "tag_handling": "keep_regional"}, [dtime(5, 0)])

    assert settings["tag_handling"] == "strip_all"


def test_a_row_that_only_repeats_what_is_already_held_changes_nothing(plugin_module, monkeypatch):
    """A subset row carrying no new information must be a silent no-op.

    This is the steady state on the live installation, and the first version of
    this code treated it as a change on every single refresh, logging each time
    and rebuilding the settings dict for no reason.
    """
    original = {"scheduled_times": "0500", "tag_handling": "keep_regional"}
    plugin = _plugin_with_db(plugin_module, monkeypatch, {"scheduled_times": "0500"})
    original_times = [dtime(5, 0)]

    settings, times = plugin._refresh_schedule_from_db(original, original_times)

    assert settings is original
    assert times is original_times


# --------------------------------------------------------------------------- #
# Refusing to adopt
# --------------------------------------------------------------------------- #
def test_the_current_schedule_is_kept_when_the_database_cannot_be_asked(plugin_module, monkeypatch):
    """A read that FAILED returns None and must never disarm a working schedule.

    The database can be unreachable for reasons that have nothing to do with the
    schedule. Treating that as "no schedule configured" would silently stop the
    daily job, which is the failure this whole mechanism exists to prevent.
    """
    plugin = _plugin_with_db(plugin_module, monkeypatch, None)
    original = {"scheduled_times": "0500", "profile_name": "a"}

    settings, times = plugin._refresh_schedule_from_db(original, [dtime(5, 0)])

    assert times == [dtime(5, 0)]
    assert settings == original


def test_the_current_schedule_is_kept_when_the_row_holds_no_settings(plugin_module, monkeypatch):
    """An EMPTY dict means the row is absent or has never been saved.

    _settings_from_db returns {} for that and None for a read it could not
    perform, and the two must not collapse: an empty row is not an instruction to
    clear a schedule that is demonstrably running. An explicitly emptied
    scheduled_times inside a real settings dict IS such an instruction, and the
    test above covers it.
    """
    plugin = _plugin_with_db(plugin_module, monkeypatch, {})
    original = {"scheduled_times": "0500", "profile_name": "a"}

    settings, times = plugin._refresh_schedule_from_db(original, [dtime(5, 0)])

    assert times == [dtime(5, 0)]
    assert settings == original


def test_an_unchanged_database_row_returns_the_same_objects(plugin_module, monkeypatch):
    """The steady state is the common case and must not rebuild anything.

    This runs on a timer for the life of the process, so agreeing with the
    database has to be cheap and must not replace the settings object the loop
    already holds.
    """
    original = {"scheduled_times": "0500", "profile_name": "a"}
    plugin = _plugin_with_db(plugin_module, monkeypatch, dict(original))
    original_times = [dtime(5, 0)]

    settings, times = plugin._refresh_schedule_from_db(original, original_times)

    assert settings is original
    assert times is original_times


def test_an_unparseable_schedule_in_the_database_is_not_adopted(plugin_module, monkeypatch):
    """A row holding a time string the parser rejects must not empty the schedule.

    _parse_scheduled_times silently drops anything that is not four digits in
    range, so a malformed entry parses to no times at all. Adopting that would
    disarm the scheduler on the strength of a value the operator can only have
    reached by editing the row directly, and it would look identical to a
    deliberate clear. An explicitly EMPTY string is still honoured, because that
    is what the interface writes when the operator clears the field.
    """
    plugin = _plugin_with_db(
        plugin_module, monkeypatch, {"scheduled_times": "not-a-time"})

    _settings, times = plugin._refresh_schedule_from_db(
        {"scheduled_times": "0500"}, [dtime(5, 0)])

    assert times == [dtime(5, 0)]


# --------------------------------------------------------------------------- #
# The loop actually calls it
# --------------------------------------------------------------------------- #
# A refresh helper nobody calls looks exactly like a fixed bug, which is how the
# dead OTA exemption shipped. These pin the wiring, not just the helper.
@pytest.fixture(scope="module")
def plugin_ast():
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in plugin.py")


def test_the_scheduler_loop_calls_the_refresh(plugin_ast):
    loop = _function(plugin_ast, "scheduler_loop")
    calls = {ast.unparse(node.func)
             for node in ast.walk(loop) if isinstance(node, ast.Call)}
    assert "self._refresh_schedule_from_db" in calls


def test_the_scheduler_loop_assigns_both_refreshed_values_back(plugin_ast):
    """Calling it and discarding the result would fix nothing."""
    loop = _function(plugin_ast, "scheduler_loop")
    targets = [
        ast.unparse(node.targets[0])
        for node in ast.walk(loop)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and ast.unparse(node.value.func) == "self._refresh_schedule_from_db"
    ]
    assert targets, "the refresh result is never assigned"
    assert any("settings" in t and "scheduled_times" in t for t in targets), targets


def test_the_scheduler_loop_rebinds_the_names_the_thread_runs_on(plugin_ast):
    """Without nonlocal the assignment creates locals and the closure keeps the old values."""
    loop = _function(plugin_ast, "scheduler_loop")
    declared = set()
    for node in ast.walk(loop):
        if isinstance(node, ast.Nonlocal):
            declared.update(node.names)
    assert {"settings", "scheduled_times"} <= declared, declared


def test_the_refresh_interval_is_long_enough_to_be_cheap(plugin_module):
    """Every worker process runs this on a timer for as long as it lives.

    The loop itself ticks every SCHEDULER_CHECK_INTERVAL seconds; re-reading the
    row on every tick would put an idle database query on that cadence in each of
    them for no benefit, because the fault this corrects persists for days.
    """
    config = plugin_module.PluginConfig
    assert config.SCHEDULER_DB_REFRESH_INTERVAL >= config.SCHEDULER_CHECK_INTERVAL
    assert config.SCHEDULER_DB_REFRESH_INTERVAL <= 3600
