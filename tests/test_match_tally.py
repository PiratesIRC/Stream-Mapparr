"""The lifetime tally of streams matched, which the public badge was built from.

The README badge was a Shields.io endpoint pointing at a Gist, refreshed from
outside the container; that gist pipeline was retired 2026-10-10. The number it shows has to come from somewhere, and the
sibling plugin iptv_checker learned the hard way that it cannot be reconstructed
after the fact: the files a plugin writes during a run get overwritten by the
next one. This plugin is slightly better off, because /data/exports is not
pruned, but an export only exists when CSV export was switched on, so it is not
a reliable record either.

So the count is recorded as it happens: one line appended per run that actually
assigned streams, and never rewritten.

WHAT COUNTS. Stream-to-channel assignments written to the database. It is a
measure of work done, not of distinct streams: a daily schedule re-matches the
same streams and counts them again each time. That matches what the sibling
plugin's own badge counts, and the README wording must not imply otherwise.

WHAT IS DELIBERATELY NOT RECORDED. A dry run, which writes nothing to the
database, and a run that assigned nothing. Recording those would grow the file
every day on an installation where an unchanged library correctly adds nothing,
while contributing zero to the number the badge shows.
"""
import ast
import contextlib
import io
import json
import os

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Stream-Mapparr", "plugin.py")


def _plugin(plugin_module, tmp_path, monkeypatch):
    """Point the tally at a temporary file, and put the real path back afterwards.

    monkeypatch rather than a plain assignment, because the path is a class
    attribute: leaking a temporary directory into it would make any later test
    that reads the real value fail for a reason that has nothing to do with it.
    """
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    monkeypatch.setattr(plugin_module.PluginConfig, "MATCH_TALLY_FILE",
                        str(tmp_path / "tally.jsonl"))
    return inst


def _lines(tmp_path):
    path = tmp_path / "tally.jsonl"
    if not path.exists():
        return []
    with open(str(path), encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
def test_a_run_that_assigned_streams_appends_one_line(plugin_module, tmp_path, monkeypatch):
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)

    plugin._record_streams_matched("add_streams_to_channels", 12, 37, dry_run=False)

    lines = _lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["streams"] == 37
    assert lines[0]["channels"] == 12
    assert lines[0]["action"] == "add_streams_to_channels"
    assert isinstance(lines[0]["ts"], (int, float))


def test_a_second_run_appends_rather_than_replacing(plugin_module, tmp_path, monkeypatch):
    """The file is the only record, so overwriting it would destroy the total."""
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)

    plugin._record_streams_matched("add_streams_to_channels", 1, 5, dry_run=False)
    plugin._record_streams_matched("match_us_ota_only", 2, 9, dry_run=False)

    lines = _lines(tmp_path)
    assert [line["streams"] for line in lines] == [5, 9]


def test_a_dry_run_records_nothing(plugin_module, tmp_path, monkeypatch):
    """A dry run writes nothing to the database, so it matched nothing."""
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)

    plugin._record_streams_matched("add_streams_to_channels", 12, 37, dry_run=True)

    assert _lines(tmp_path) == []


def test_a_run_that_assigned_nothing_records_nothing(plugin_module, tmp_path, monkeypatch):
    """With Overwrite off and an unchanged library a run correctly adds nothing.

    That is the common case on a daily schedule, and a line per day carrying a
    zero would grow the file forever while contributing nothing to the total.
    """
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)

    plugin._record_streams_matched("add_streams_to_channels", 40, 0, dry_run=False)

    assert _lines(tmp_path) == []


# --------------------------------------------------------------------------- #
# It must never break a run
# --------------------------------------------------------------------------- #
def test_a_tally_that_cannot_be_written_does_not_raise(plugin_module, tmp_path, monkeypatch):
    """A counter that exists to feed a badge must never be able to fail a run."""
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)
    # A directory where the file should be: opening it for append raises.
    os.makedirs(str(tmp_path / "tally.jsonl"), exist_ok=True)

    plugin._record_streams_matched("add_streams_to_channels", 1, 1, dry_run=False)


def test_a_non_numeric_count_does_not_raise(plugin_module, tmp_path, monkeypatch):
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)

    plugin._record_streams_matched("add_streams_to_channels", None, None, dry_run=False)

    assert _lines(tmp_path) == []


# --------------------------------------------------------------------------- #
# Both assigning actions actually call it, from a finally
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def plugin_ast():
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in plugin.py")


ASSIGNING_ACTIONS = ["add_streams_to_channels_action", "match_us_ota_only_action"]


@pytest.mark.parametrize("action", ASSIGNING_ACTIONS)
def test_the_assigning_action_records_its_tally(plugin_ast, action):
    func = _function(plugin_ast, action)
    calls = {ast.unparse(n.func) for n in ast.walk(func) if isinstance(n, ast.Call)}
    assert "self._record_streams_matched" in calls


@pytest.mark.parametrize("action", ASSIGNING_ACTIONS)
def test_the_tally_is_written_from_a_finally(plugin_ast, action):
    """A run that fails part way still assigned whatever it assigned.

    Recording only on the success path would lose those, and would lose them
    silently, which is the shape that makes a published number quietly wrong.
    """
    func = _function(plugin_ast, action)
    in_finally = False
    for node in ast.walk(func):
        if isinstance(node, ast.Try) and node.finalbody:
            for sub in node.finalbody:
                for call in ast.walk(sub):
                    if (isinstance(call, ast.Call)
                            and ast.unparse(call.func) == "self._record_streams_matched"):
                        in_finally = True
    assert in_finally, f"{action} does not record its tally from a finally block"


def test_the_tally_is_declared_under_the_data_volume(plugin_ast):
    """Read from the SOURCE, because the test harness redirects /data elsewhere.

    /data is a named Docker volume and survives the container being recreated,
    which a lifetime tally has to. Asserting the runtime value would assert the
    temporary directory the test fixtures substitute, and would pass whatever
    the source said.
    """
    declared = None
    for node in ast.walk(plugin_ast):
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "MATCH_TALLY_FILE"
                        for t in node.targets)
                and isinstance(node.value, ast.Constant)):
            declared = node.value.value
    assert declared is not None, "MATCH_TALLY_FILE is not declared"
    assert declared.startswith("/data/"), declared
    assert declared.endswith(".jsonl"), declared


# --------------------------------------------------------------------------- #
# The append holds the tally lock (the one sync_tally_tail takes)
# --------------------------------------------------------------------------- #
class _RecordingLock:
    """Stands in for storage.tally_lock: records that it was entered and what the file held
    the moment it was released, so the test sees the append happen inside the lock."""

    def __init__(self, path):
        self.path = path
        self.entered = 0
        self.lines_at_release = None

    @contextlib.contextmanager
    def __call__(self, layout):
        self.entered += 1
        yield
        with open(self.path, encoding="utf-8") as fh:
            self.lines_at_release = fh.read().count("\n")


def _non_legacy_layout(plugin_module, tmp_path):
    return plugin_module.storage.Layout(False, str(tmp_path), str(tmp_path), "stream-mapparr", "test")


def test_the_tally_append_runs_inside_the_tally_lock(plugin_module, tmp_path, monkeypatch):
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)
    tally = str(tmp_path / "tally.jsonl")
    lock = _RecordingLock(tally)
    monkeypatch.setattr(plugin_module.storage, "tally_lock", lock)
    monkeypatch.setattr(plugin_module.storage, "sync_tally_tail", lambda *a, **k: None)
    monkeypatch.setattr(plugin_module.PluginConfig, "STORAGE_LAYOUT", _non_legacy_layout(plugin_module, tmp_path))
    monkeypatch.setattr(plugin, "_report_usage", lambda *a, **k: None, raising=False)

    plugin._record_streams_matched("add_streams_to_channels", 3, 7, dry_run=False)

    assert lock.entered == 1, "the tally append did not take the tally lock"
    assert lock.lines_at_release == 1, "the line was not written before the lock was released"


def test_the_tail_sync_runs_before_the_lock_and_not_inside_it(plugin_module, tmp_path, monkeypatch):
    """sync_tally_tail takes the same lock, which is not reentrant: nesting it deadlocks."""
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)
    events = []

    @contextlib.contextmanager
    def lock(layout):
        events.append("lock-enter")
        yield
        events.append("lock-exit")

    monkeypatch.setattr(plugin_module.storage, "tally_lock", lock)
    monkeypatch.setattr(plugin_module.storage, "sync_tally_tail",
                        lambda *a, **k: events.append("sync"))
    monkeypatch.setattr(plugin_module.PluginConfig, "STORAGE_LAYOUT", _non_legacy_layout(plugin_module, tmp_path))
    monkeypatch.setattr(plugin, "_report_usage", lambda *a, **k: None, raising=False)

    plugin._record_streams_matched("add_streams_to_channels", 3, 7, dry_run=False)

    assert events == ["sync", "lock-enter", "lock-exit"]


def test_a_tally_lock_that_fails_records_nothing_and_does_not_raise(plugin_module, tmp_path, monkeypatch):
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)

    @contextlib.contextmanager
    def failing(layout):
        raise OSError("lock file unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(plugin_module.storage, "tally_lock", failing)
    monkeypatch.setattr(plugin_module.storage, "sync_tally_tail", lambda *a, **k: None)
    monkeypatch.setattr(plugin_module.PluginConfig, "STORAGE_LAYOUT", _non_legacy_layout(plugin_module, tmp_path))

    plugin._record_streams_matched("add_streams_to_channels", 3, 7, dry_run=False)

    assert _lines(tmp_path) == []


def test_legacy_mode_takes_no_tally_lock(plugin_module, tmp_path, monkeypatch):
    """Legacy mode has no data directory to hold a lock in, so the append runs unlocked.

    Patches the vendored flock helper underneath storage.tally_lock, so the test sees
    whether any lock is taken at all, not just whether the plugin asked for one.
    """
    plugin = _plugin(plugin_module, tmp_path, monkeypatch)
    taken = []

    @contextlib.contextmanager
    def recording_flock(path):
        taken.append(path)
        yield

    monkeypatch.setattr(plugin_module.storage._ps, "_lock", recording_flock)
    assert plugin_module.PluginConfig.STORAGE_LAYOUT.legacy is True

    plugin._record_streams_matched("add_streams_to_channels", 3, 7, dry_run=False)

    assert taken == []
    assert len(_lines(tmp_path)) == 1


def test_the_real_tally_lock_is_a_no_op_in_legacy_mode(plugin_module, tmp_path):
    storage = plugin_module.storage
    layout = storage.Layout(True, None, None, "stream-mapparr", "test")
    with storage.tally_lock(layout):
        pass
    assert not (tmp_path / storage.SPEC.tally_lock).exists()


def test_the_tally_lock_takes_the_lock_file_beside_the_tally(plugin_module, tmp_path, monkeypatch):
    """Platform-independent: records the path the real helper asks the flock layer to lock."""
    storage = plugin_module.storage
    locked = []

    @contextlib.contextmanager
    def recording_flock(path):
        locked.append(path)
        yield

    monkeypatch.setattr(storage._ps, "_lock", recording_flock)
    layout = storage.Layout(False, str(tmp_path), str(tmp_path), "stream-mapparr", "test")
    with storage.tally_lock(layout):
        assert locked == [os.path.join(str(tmp_path), storage.SPEC.tally_lock)]


@pytest.mark.skipif(os.name == "nt", reason="fcntl locks run on POSIX only")
def test_the_real_tally_lock_file_lives_beside_the_tally(plugin_module, tmp_path):
    storage = plugin_module.storage
    layout = storage.Layout(False, str(tmp_path), str(tmp_path), "stream-mapparr", "test")
    with storage.tally_lock(layout):
        assert os.path.exists(os.path.join(str(tmp_path), storage.SPEC.tally_lock))
