"""The matching loops must hand the worker back between units of work.

Dispatcharr's uWSGI runs gevent with early monkey-patching, so a thread inside a
plugin is a greenlet: it keeps the entire worker to itself until it yields.
Nothing in the matching loops yielded, so a run made the worker unresponsive to
every other request for its whole duration. That is the half of bug-117 the
sync-versus-background gate does not solve: the gate only decides WHICH worker
pays, never that none of them does.

The regex pre-processing pass already had this treatment and is the shape copied
here. These tests pin both halves: that the yield really is a yield, and that
every loop that does per-item matching work actually calls it. The second half
matters more, because a helper nobody calls looks exactly like a fixed bug.
"""
import ast
import os

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Stream-Mapparr", "plugin.py")

# Each entry is an action and the loop inside it that does the per-item work.
# The loop is identified by what it iterates, because line numbers move.
HOT_LOOPS = [
    ("preview_changes_action", "channel_groups.items()"),
    ("add_streams_to_channels_action", "channel_groups.items()"),
    ("match_us_ota_only_action", "enumerate(channels, 1)"),
    ("sort_streams_action", "channels_with_multiple_streams"),
]


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


@pytest.fixture(scope="module")
def plugin_ast():
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in plugin.py")


def _loop_iterating(func, needle):
    for node in ast.walk(func):
        if isinstance(node, ast.For) and needle in ast.unparse(node.iter):
            return node
    raise AssertionError(f"no loop over {needle} in {func.name}")


def _calls_in(node):
    return {ast.unparse(sub.func) for sub in ast.walk(node) if isinstance(sub, ast.Call)}


# --------------------------------------------------------------------------- #
# The yield itself
# --------------------------------------------------------------------------- #
def test_cooperative_yield_sleeps_zero(plugin_module, monkeypatch):
    """time.sleep(0) is gevent.sleep(0) once monkey-patching has run."""
    calls = []
    monkeypatch.setattr(plugin_module.time, "sleep", lambda seconds: calls.append(seconds))
    _bare(plugin_module)._cooperative_yield()
    assert calls == [0]


def test_cooperative_yield_is_harmless_unpatched(plugin_module):
    """Outside Dispatcharr nothing has patched time.sleep, so this does nothing.

    It must still not raise, because the same code runs in tests and in any
    environment where the plugin is imported without gevent.
    """
    assert _bare(plugin_module)._cooperative_yield() is None


# --------------------------------------------------------------------------- #
# Every hot loop must call it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action,iterated", HOT_LOOPS)
def test_hot_loop_yields(plugin_ast, action, iterated):
    loop = _loop_iterating(_function(plugin_ast, action), iterated)
    assert "self._cooperative_yield" in _calls_in(loop), (
        f"the loop over {iterated} in {action} runs matching work without ever "
        f"handing the worker back")


# Not a loop in plugin.py. The placeholder family scan walks the stream names
# inside placeholder_scan.py, which has no Django import and no knowledge of
# gevent, so the action hands it the yield as a callback instead. It is counted
# here because the pinned total below must account for every call site.
CALLBACK_SITES = [
    ("scan_placeholder_names_action", "scan.scan_families"),
]


@pytest.mark.parametrize("action,called", CALLBACK_SITES)
def test_the_scan_hands_its_walk_a_yield(plugin_ast, action, called):
    """The walk reads about 25,000 names and applies every configured
    placeholder pattern to each, inside the request. Without this the worker is
    held for the whole run, which is the half of bug-117 that the sync-versus-
    background gate does not solve."""
    func = _function(plugin_ast, action)
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == called:
            source = ast.unparse(node)
            assert "_cooperative_yield" in source, (
                f"{action} calls {called} without handing it a yield")
            # The VALUES, not the argument names. Setting them to None keeps the
            # names in the source and removes the bound, and that mutation went
            # uncaught until this assertion was tightened.
            assert "max_name_len=cfg.REGEX_NAME_MAX_LEN" in source, (
                f"{action} calls {called} without the input cap, so an operator "
                f"pattern that backtracks has nothing bounding the name length")
            assert "budget_seconds=cfg.REGEX_PASS_BUDGET_S" in source, (
                f"{action} calls {called} without the time budget, so a slow "
                f"pattern holds the worker for as long as it takes")
            return
    raise AssertionError(f"no call to {called} in {action}")


def test_yield_call_sites_are_pinned(plugin_ast):
    """Pin the count so a new matching loop is a deliberate decision.

    A previous fix in this plugin shipped looking correct and changed nothing
    because only one of the places that needed it was updated. Counting the call
    sites is what catches that.
    """
    module_calls = [node for node in ast.walk(plugin_ast)
                    if isinstance(node, ast.Call)
                    and ast.unparse(node.func).endswith("_cooperative_yield")]
    expected = len(HOT_LOOPS) + len(CALLBACK_SITES)
    assert len(module_calls) == expected, (
        f"expected one call per hot loop ({len(HOT_LOOPS)}) plus one per callback "
        f"site ({len(CALLBACK_SITES)}), found {len(module_calls)}")
