"""The lifetime tally of streams matched, which the public badge is built from.

The README badge is a Shields.io endpoint pointing at a Gist, refreshed from
outside the container. The number it shows has to come from somewhere, and the
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
