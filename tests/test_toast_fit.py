"""Messages that a Dispatcharr toast can actually show.

A toast is clipped at roughly 280 characters, taken from the MIDDLE, with no
ellipsis and with newlines collapsed into one paragraph. So an over-long message
does not lose its tail, it loses its middle, and nothing on screen says so. The
operator reads a beginning and an end that were never adjacent and has no way to
know what fell out.

Measured on this build: the regex rule test emits up to 20 before-and-after
samples of up to about 165 characters each, so roughly nine tenths of its output
was being discarded invisibly.

The rule adopted here is to drop whole lines from the END, keep the ordering the
action chose, and say plainly how many lines were dropped. That is worse than
showing everything and better than pretending to.
"""


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_a_short_message_is_untouched(plugin_module):
    plugin = _bare(plugin_module)
    assert plugin._fit_toast(["one", "two"]) == "one\ntwo"


def test_no_parts_gives_an_empty_string(plugin_module):
    assert _bare(plugin_module)._fit_toast([]) == ""


def test_an_over_long_message_is_cut_to_the_budget(plugin_module):
    plugin = _bare(plugin_module)
    parts = [f"line {i} " + "x" * 60 for i in range(30)]
    out = plugin._fit_toast(parts)
    assert len(out) <= plugin_module.PluginConfig.TOAST_BUDGET_CHARS


def test_the_cut_says_how_many_lines_it_dropped(plugin_module):
    plugin = _bare(plugin_module)
    parts = [f"line {i} " + "x" * 60 for i in range(30)]
    out = plugin._fit_toast(parts)
    assert "more line" in out, "a silent truncation is the thing being fixed"
    assert "27" in out or "28" in out or "26" in out


def test_the_earliest_lines_survive(plugin_module):
    """Actions put the summary first, so the cut must come off the end."""
    plugin = _bare(plugin_module)
    parts = ["THE HEADLINE"] + [f"detail {i} " + "y" * 60 for i in range(30)]
    out = plugin._fit_toast(parts)
    assert out.startswith("THE HEADLINE")


def test_a_single_over_long_line_is_still_capped(plugin_module):
    """One giant line cannot be solved by dropping later lines."""
    plugin = _bare(plugin_module)
    out = plugin._fit_toast(["z" * 5000])
    assert len(out) <= plugin_module.PluginConfig.TOAST_BUDGET_CHARS


def test_the_budget_matches_what_dispatcharr_shows(plugin_module):
    """Pinned so a future edit cannot quietly widen it past what renders."""
    assert plugin_module.PluginConfig.TOAST_BUDGET_CHARS == 280


# --------------------------------------------------------------------------- #
# The actions that build a message from a list must use it
# --------------------------------------------------------------------------- #
def test_every_joined_message_goes_through_the_fitter(plugin_module):
    """A message assembled from a per-item list has no length bound at all.

    Counting the call sites is what catches the half-applied fix: an earlier
    change in this plugin shipped looking correct because only one of the places
    that needed it was updated.
    """
    import ast
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    unbounded = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not (isinstance(key, ast.Constant) and key.value == "message"):
                continue
            rendered = ast.unparse(value)
            if ".join(" in rendered and "_fit_toast" not in rendered:
                unbounded.append((value.lineno, rendered[:70]))
    assert not unbounded, (
        "message values built from a list without a length bound: " + str(unbounded))
