"""Age-based cleanup of this plugin's CSV exports.

/data/exports IS SHARED. Measured on the live installation on 2026-09-05, it
held 130 files belonging to at least seven plugins:

    stream_mapparr_*            65
    epg_janitor_*               26
    event_channel_managarr_*    14
    lineuparr_*                 10
    iptv_checker_results_*       5
    channel_mapparr_*            4
    ecm_test                     1   (no .csv suffix at all)

So selection is the whole risk. A glob of *.csv, or a match on the suffix alone,
deletes other projects' data. With a seven day rule that is over a hundred files
on the first run, and they are not recoverable.

Every test below that concerns the AGE rule or the OFF-BY-DEFAULT rule uses
SEVERAL old files on purpose. The rule that one file always survives will keep a
lone file no matter what, so a single-file test passes while the guard it names
is deleted. That happened when the sibling plugin iptv_checker built this, and
mutation testing is what caught it; reading the tests did not.
"""
import ast
import io
import os

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Stream-Mapparr", "plugin.py")

NOW = 1_700_000_000.0
DAY = 86400.0


def _decide(plugin_module, entries, days=7, now=NOW, protect=None):
    return plugin_module.Plugin._csv_exports_to_delete(entries, days, now, protect)


def _old(n_days):
    return NOW - n_days * DAY


# --------------------------------------------------------------------------- #
# Selection: the whole risk
# --------------------------------------------------------------------------- #
def test_another_plugins_exports_are_never_deleted(plugin_module):
    """The measured live contents of the shared directory, all long expired."""
    entries = [
        ("epg_janitor_automatch_applied_20260101_000000.csv", _old(90)),
        ("event_channel_managarr_applied_20260101_000000.csv", _old(90)),
        ("lineuparr_match_applied_20260101_000000.csv", _old(90)),
        ("iptv_checker_results_20260101_000000.csv", _old(90)),
        ("channel_mapparr_preview_20260101_000000.csv", _old(90)),
        ("stream_mapparr_sorted_20260101_000000.csv", _old(90)),
        ("stream_mapparr_preview_20260101_000000.csv", _old(89)),
        ("stream_mapparr_20260102_000000.csv", _old(88)),
    ]
    doomed = _decide(plugin_module, entries)
    assert all(name.startswith("stream_mapparr_") for name in doomed), doomed
    assert len(doomed) == 2, doomed


def test_a_file_without_the_csv_suffix_is_left_alone(plugin_module):
    """The live directory contains a file named ecm_test with no suffix at all."""
    entries = [
        ("stream_mapparr_sorted_20260101_000000", _old(90)),
        ("stream_mapparr_notes.txt", _old(90)),
        ("stream_mapparr_a_20260101_000000.csv", _old(90)),
        ("stream_mapparr_b_20260101_000000.csv", _old(89)),
        ("stream_mapparr_c_20260101_000000.csv", _old(88)),
    ]
    doomed = _decide(plugin_module, entries)
    assert all(name.endswith(".csv") for name in doomed), doomed


def test_a_name_that_merely_contains_the_prefix_is_not_ours(plugin_module):
    """Selection is on how the name STARTS, not on the prefix appearing anywhere."""
    entries = [
        ("backup_of_stream_mapparr_20260101_000000.csv", _old(90)),
        ("stream_mapparr_a_20260101_000000.csv", _old(90)),
        ("stream_mapparr_b_20260101_000000.csv", _old(89)),
        ("stream_mapparr_c_20260101_000000.csv", _old(88)),
    ]
    assert "backup_of_stream_mapparr_20260101_000000.csv" not in _decide(plugin_module, entries)


# --------------------------------------------------------------------------- #
# Off unless configured. SEVERAL old files, or the survivor rule hides the bug.
# --------------------------------------------------------------------------- #
def _four_old(plugin_module):
    return [("stream_mapparr_a_20260101_000000.csv", _old(90)),
            ("stream_mapparr_b_20260101_000000.csv", _old(80)),
            ("stream_mapparr_c_20260101_000000.csv", _old(70)),
            ("stream_mapparr_d_20260101_000000.csv", _old(60))]


@pytest.mark.parametrize("value", [0, -1, -30, "", "   ", None, "abc", "7.5.1", [], {}])
def test_nothing_is_deleted_unless_a_positive_number_of_days_is_set(plugin_module, value):
    """Nobody loses a file merely by upgrading to a version that has this."""
    assert _decide(plugin_module, _four_old(plugin_module), days=value) == []


def test_a_positive_number_given_as_text_is_honoured(plugin_module):
    """Dispatcharr can hand a number setting back as a string."""
    assert len(_decide(plugin_module, _four_old(plugin_module), days="30")) == 3


# --------------------------------------------------------------------------- #
# The age rule itself
# --------------------------------------------------------------------------- #
def test_files_older_than_the_limit_go_and_newer_ones_stay(plugin_module):
    entries = [
        ("stream_mapparr_ancient_20260101_000000.csv", _old(30)),
        ("stream_mapparr_old_20260101_000000.csv", _old(20)),
        ("stream_mapparr_fresh_20260101_000000.csv", _old(2)),
        ("stream_mapparr_newest_20260101_000000.csv", _old(1)),
    ]
    doomed = _decide(plugin_module, entries, days=7)
    assert doomed == ["stream_mapparr_ancient_20260101_000000.csv",
                      "stream_mapparr_old_20260101_000000.csv"], doomed


def test_a_file_exactly_the_retention_age_is_kept(plugin_module):
    """Exactly N days old is not OLDER than N days. Several files, so the
    survivor rule cannot be what keeps it."""
    entries = [
        ("stream_mapparr_exact_20260101_000000.csv", _old(7)),
        ("stream_mapparr_older_20260101_000000.csv", _old(9)),
        ("stream_mapparr_oldest_20260101_000000.csv", _old(11)),
        ("stream_mapparr_new_20260101_000000.csv", _old(1)),
    ]
    doomed = _decide(plugin_module, entries, days=7)
    assert "stream_mapparr_exact_20260101_000000.csv" not in doomed, doomed
    assert len(doomed) == 2, doomed


# --------------------------------------------------------------------------- #
# The two files that must always survive
# --------------------------------------------------------------------------- #
def test_the_newest_file_survives_even_when_everything_is_old(plugin_module):
    """A small retention number must not be able to empty the directory."""
    doomed = _decide(plugin_module, _four_old(plugin_module), days=1)
    assert len(doomed) == 3
    assert "stream_mapparr_d_20260101_000000.csv" not in doomed


def test_the_file_just_written_is_never_deleted(plugin_module):
    """Whatever the age arithmetic says. It is the point of the run."""
    entries = _four_old(plugin_module) + [
        ("stream_mapparr_justwritten_20260101_000000.csv", _old(99))]
    doomed = _decide(plugin_module, entries, days=1,
                     protect="stream_mapparr_justwritten_20260101_000000.csv")
    assert "stream_mapparr_justwritten_20260101_000000.csv" not in doomed
    # It is also the survivor, so every other old file of ours goes.
    assert len(doomed) == 4, doomed


# --------------------------------------------------------------------------- #
# A modification time that is not a number
# --------------------------------------------------------------------------- #
def test_a_modification_time_that_is_not_a_number_is_ignored(plugin_module):
    """Keeping it is worse than dropping it, and not in an obvious way.

    Every comparison against a not-a-number value is false, so it wins any
    "which of these is newest" test and becomes the one file kept, and every
    real file is deleted instead. That is the opposite of the intended
    behaviour, and it survived a first round of tests on the sibling plugin.
    """
    nan = float("nan")
    entries = [
        ("stream_mapparr_broken_20260101_000000.csv", nan),
        ("stream_mapparr_a_20260101_000000.csv", _old(90)),
        ("stream_mapparr_b_20260101_000000.csv", _old(80)),
        ("stream_mapparr_c_20260101_000000.csv", _old(70)),
    ]
    doomed = _decide(plugin_module, entries, days=1)
    assert "stream_mapparr_broken_20260101_000000.csv" not in doomed, "unknown age is not old"
    assert "stream_mapparr_c_20260101_000000.csv" not in doomed, \
        "the newest REAL file must be the survivor, not the unreadable one"
    assert len(doomed) == 2, doomed


@pytest.mark.parametrize("mtime", ["not a time", None, [], {}])
def test_an_unreadable_modification_time_is_skipped(plugin_module, mtime):
    entries = _four_old(plugin_module) + [("stream_mapparr_bad_20260101_000000.csv", mtime)]
    assert "stream_mapparr_bad_20260101_000000.csv" not in _decide(
        plugin_module, entries, days=1)


# --------------------------------------------------------------------------- #
# The wrapper that touches the filesystem
# --------------------------------------------------------------------------- #
def test_the_wrapper_deletes_and_counts(plugin_module, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    for name, age in [("stream_mapparr_a.csv", 90), ("stream_mapparr_b.csv", 80),
                      ("stream_mapparr_c.csv", 1), ("epg_janitor_x.csv", 90)]:
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        stamp = NOW - age * DAY
        os.utime(str(path), (stamp, stamp))
    monkeypatch.setattr(plugin_module.time, "time", lambda: NOW)

    removed = plugin_module.Plugin.__new__(plugin_module.Plugin)._prune_csv_exports(7)

    assert removed == 2
    # Files only: the test fixtures create a directory alongside them.
    left = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert left == ["epg_janitor_x.csv", "stream_mapparr_c.csv"], left


def test_a_failed_delete_does_not_raise(plugin_module, tmp_path, monkeypatch):
    """Tidying up must never turn a successful export into a reported error."""
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    for name in ("stream_mapparr_a.csv", "stream_mapparr_b.csv", "stream_mapparr_c.csv"):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        os.utime(str(path), (NOW - 90 * DAY, NOW - 90 * DAY))
    monkeypatch.setattr(plugin_module.time, "time", lambda: NOW)
    monkeypatch.setattr(plugin_module.os, "remove",
                        lambda p: (_ for _ in ()).throw(OSError("denied")))

    assert plugin_module.Plugin.__new__(plugin_module.Plugin)._prune_csv_exports(7) == 0


def test_a_missing_directory_does_not_raise(plugin_module, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR",
                        str(tmp_path / "does-not-exist"))
    assert plugin_module.Plugin.__new__(plugin_module.Plugin)._prune_csv_exports(7) == 0


# --------------------------------------------------------------------------- #
# It has to actually be called
# --------------------------------------------------------------------------- #
EXPORTING_ACTIONS = ["preview_changes_action", "add_streams_to_channels_action",
                     "match_us_ota_only_action", "sort_streams_action"]


@pytest.fixture(scope="module")
def plugin_ast():
    with io.open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


@pytest.mark.parametrize("action", EXPORTING_ACTIONS)
def test_every_action_that_writes_an_export_prunes_afterwards(plugin_ast, action):
    """A helper nobody calls looks exactly like a finished feature."""
    func = next(n for n in ast.walk(plugin_ast)
                if isinstance(n, ast.FunctionDef) and n.name == action)
    calls = {ast.unparse(c.func) for c in ast.walk(func) if isinstance(c, ast.Call)}
    assert "self._prune_csv_exports" in calls


def test_clear_all_exports_still_clears_everything(plugin_ast):
    """Someone pressing it expects everything cleared, not everything old."""
    func = next(n for n in ast.walk(plugin_ast)
                if isinstance(n, ast.FunctionDef) and n.name == "clear_csv_exports_action")
    calls = {ast.unparse(c.func) for c in ast.walk(func) if isinstance(c, ast.Call)}
    assert "self._prune_csv_exports" not in calls


def test_the_retention_setting_is_offered_and_defaults_to_keeping_everything(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    field = next((f for f in inst.fields
                  if f.get("id") == "csv_export_retention_days"), None)
    assert field is not None, "the setting is not offered"
    assert field["type"] == "number"
    assert field.get("default") == 0
