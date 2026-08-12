"""The scheduler bootstraps from the DATABASE, not only from a cache file.

Measured on the live installation on 2026-08-12: the plugin row in the database
held a daily 05:00 schedule and the plugin was enabled, but the file the
scheduler actually bootstrapped from held an empty string for the same setting
and had not been written since 2026-07-11. So every worker logged "No scheduled
times configured" and the daily job had not run for a month. Nothing reported a
problem, because from the scheduler's point of view there was no schedule.

The file is a cache of what the interface last wrote. The database row is what
the interface actually saved. When they disagree the database is right, and the
file is rewritten so the disagreement does not come back.

Cost matters here: the plugin is re-instantiated on Dispatcharr's per-request
path, so the database is consulted at most once per worker process, and only
until it has been read successfully. A failed read is retried, because the
plugin can be constructed before the database is accepting connections during
container start, and giving up then would leave the schedule dead until the next
restart.
"""
import json


def _plugin(plugin_module, tmp_path, file_settings=None):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.settings_file = str(tmp_path / "settings.json")
    inst.saved_settings = dict(file_settings) if file_settings is not None else {}
    if file_settings is not None:
        with open(inst.settings_file, "w", encoding="utf-8") as handle:
            json.dump(file_settings, handle)
    return inst


def _fresh_state(plugin_module):
    """A scheduler state with no record of a previous database read."""
    state = plugin_module._scheduler_state()
    state.db_checked = False
    return state


# --------------------------------------------------------------------------- #
# Reading the row
# --------------------------------------------------------------------------- #
def test_reading_the_row_returns_none_when_the_orm_is_absent(plugin_module, tmp_path):
    """Outside Dispatcharr the model cannot be imported, and that is not an error.

    The plugin is imported by the test suite and by any tooling that does not
    have Django configured, so this path has to be quiet.
    """
    plugin = _plugin(plugin_module, tmp_path)
    assert plugin._settings_from_db() is None


# --------------------------------------------------------------------------- #
# Reconciling
# --------------------------------------------------------------------------- #
def test_a_schedule_only_in_the_database_is_adopted(plugin_module, tmp_path, monkeypatch):
    """The exact live failure: database says 0500, cache file says nothing."""
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": "", "profile_name": "a"})
    _fresh_state(plugin_module)
    plugin.saved_settings = {"scheduled_times": "", "profile_name": "a"}
    monkeypatch.setattr(type(plugin), "_settings_from_db",
                        lambda self: {"scheduled_times": "0500", "profile_name": "a"})
    plugin._reconcile_schedule_with_db()
    assert plugin.saved_settings["scheduled_times"] == "0500"


def test_the_cache_file_is_rewritten_so_the_drift_does_not_return(plugin_module, tmp_path, monkeypatch):
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": ""})
    _fresh_state(plugin_module)
    plugin.saved_settings = {"scheduled_times": ""}
    monkeypatch.setattr(type(plugin), "_settings_from_db",
                        lambda self: {"scheduled_times": "0500"})
    plugin._reconcile_schedule_with_db()
    on_disk = json.load(open(plugin.settings_file, encoding="utf-8"))
    assert on_disk["scheduled_times"] == "0500"


def test_agreement_changes_nothing(plugin_module, tmp_path, monkeypatch):
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": "0500", "extra": "keep me"})
    _fresh_state(plugin_module)
    plugin.saved_settings = {"scheduled_times": "0500", "extra": "keep me"}
    monkeypatch.setattr(type(plugin), "_settings_from_db",
                        lambda self: {"scheduled_times": "0500"})
    plugin._reconcile_schedule_with_db()
    assert plugin.saved_settings == {"scheduled_times": "0500", "extra": "keep me"}


def test_a_schedule_removed_in_the_database_is_also_adopted(plugin_module, tmp_path, monkeypatch):
    """The database wins in BOTH directions, or it is not the source of truth.

    Someone who clears the schedule in the interface must not have it revived by
    a stale cache file on the next restart.
    """
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": "0500"})
    _fresh_state(plugin_module)
    plugin.saved_settings = {"scheduled_times": "0500"}
    monkeypatch.setattr(type(plugin), "_settings_from_db",
                        lambda self: {"scheduled_times": ""})
    plugin._reconcile_schedule_with_db()
    assert plugin.saved_settings["scheduled_times"] == ""


# --------------------------------------------------------------------------- #
# Cost, and recovery from a database that is not ready yet
# --------------------------------------------------------------------------- #
def test_the_database_is_read_once_per_process(plugin_module, tmp_path, monkeypatch):
    """Plugin construction is on the per-request path, so this cannot repeat."""
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": ""})
    _fresh_state(plugin_module)
    calls = []

    def _counted(self):
        calls.append(1)
        return {"scheduled_times": "0500"}

    monkeypatch.setattr(type(plugin), "_settings_from_db", _counted)
    plugin._reconcile_schedule_with_db()
    plugin._reconcile_schedule_with_db()
    plugin._reconcile_schedule_with_db()
    assert len(calls) == 1


def test_a_failed_read_is_retried(plugin_module, tmp_path, monkeypatch):
    """The plugin can be constructed before the database accepts connections.

    Marking the check done on a failure would leave the schedule dead until the
    next container restart, which is the failure being fixed, arriving by a
    different route.
    """
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": ""})
    _fresh_state(plugin_module)
    results = [None, None, {"scheduled_times": "0500"}]
    monkeypatch.setattr(type(plugin), "_settings_from_db",
                        lambda self: results.pop(0))
    plugin._reconcile_schedule_with_db()
    plugin._reconcile_schedule_with_db()
    assert plugin.saved_settings["scheduled_times"] == ""
    plugin._reconcile_schedule_with_db()
    assert plugin.saved_settings["scheduled_times"] == "0500"


def test_a_missing_row_stops_further_reads(plugin_module, tmp_path, monkeypatch):
    """A plugin with no row yet is a complete answer, not a failed read.

    Returning an empty dict distinguishes "the database answered, there is no
    configuration" from "the database could not be reached".
    """
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": ""})
    _fresh_state(plugin_module)
    calls = []
    monkeypatch.setattr(type(plugin), "_settings_from_db",
                        lambda self: (calls.append(1), {})[1])
    plugin._reconcile_schedule_with_db()
    plugin._reconcile_schedule_with_db()
    assert len(calls) == 1


def test_reconciling_never_raises(plugin_module, tmp_path, monkeypatch):
    """This runs during plugin construction, so it must not be able to break it."""
    plugin = _plugin(plugin_module, tmp_path, {"scheduled_times": ""})
    _fresh_state(plugin_module)

    def _explode(self):
        raise RuntimeError("database on fire")

    monkeypatch.setattr(type(plugin), "_settings_from_db", _explode)
    plugin._reconcile_schedule_with_db()
    assert plugin.saved_settings["scheduled_times"] == ""


# --------------------------------------------------------------------------- #
# It has to be wired into the bootstrap, or none of the above runs
# --------------------------------------------------------------------------- #
def test_the_bootstrap_reconciles_before_arming(plugin_module):
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    block = src[src.index("def _load_settings"):]
    block = block[:block.index("def _save_settings")]
    assert "_reconcile_schedule_with_db(" in block
    assert block.index("_reconcile_schedule_with_db(") < block.index("_start_background_scheduler(")
