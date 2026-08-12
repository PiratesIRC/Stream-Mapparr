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


def test_yield_call_sites_are_pinned(plugin_ast):
    """Pin the count so a new matching loop is a deliberate decision.

    A previous fix in this plugin shipped looking correct and changed nothing
    because only one of the places that needed it was updated. Counting the call
    sites is what catches that.
    """
    module_calls = [node for node in ast.walk(plugin_ast)
                    if isinstance(node, ast.Call)
                    and ast.unparse(node.func).endswith("_cooperative_yield")]
    assert len(module_calls) == len(HOT_LOOPS), (
        f"expected one call per hot loop ({len(HOT_LOOPS)}), found {len(module_calls)}")
