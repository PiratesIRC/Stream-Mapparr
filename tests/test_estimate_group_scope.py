"""Sizing a run against the CURRENT group selection, not the last one.

The runtime estimate counts channel groups out of the cached file written by the
last load. That cache is whatever the previous run loaded, so changing the
Channel Groups setting and pressing Match and Assign produced an estimate for the
OLD selection. The estimate decides synchronous versus background dispatch, and
running a matching loop synchronously occupies a whole uWSGI worker, so sizing
the wrong job is not a cosmetic error.

Narrowing is done by group NAME rather than by id, because the setting stores
names and the cached rows already carry them. That keeps the estimate free of
database queries, which is the reason it can run on the way into an action.
"""


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def _ch(cid, group):
    return {"id": cid, "name": f"Channel {cid}", "channel_group__name": group,
            "channel_group_id": hash(group) % 1000}


CACHE = [_ch(1, "US: News"), _ch(2, "US: News"), _ch(3, "US: Sports"), _ch(4, "UK: All")]


# --------------------------------------------------------------------------- #
# No selection means everything, which is what an empty setting means elsewhere
# --------------------------------------------------------------------------- #
def test_no_selection_keeps_every_cached_channel(plugin_module):
    plugin = _bare(plugin_module)
    assert plugin._estimate_scope_channels(CACHE, {}) == CACHE
    assert plugin._estimate_scope_channels(CACHE, {"selected_groups": ""}) == CACHE
    assert plugin._estimate_scope_channels(CACHE, {"selected_groups": None}) == CACHE


# --------------------------------------------------------------------------- #
# Include mode
# --------------------------------------------------------------------------- #
def test_include_keeps_only_the_named_groups(plugin_module):
    plugin = _bare(plugin_module)
    kept = plugin._estimate_scope_channels(CACHE, {"selected_groups": "US: News"})
    assert [c["id"] for c in kept] == [1, 2]


def test_include_accepts_several_groups_and_ignores_spacing(plugin_module):
    plugin = _bare(plugin_module)
    kept = plugin._estimate_scope_channels(
        CACHE, {"selected_groups": " US: News , UK: All "})
    assert [c["id"] for c in kept] == [1, 2, 4]


def test_include_naming_a_group_the_cache_never_saw_gives_up(plugin_module):
    """The cache was built from a narrower selection, so it cannot size this job.

    Returning None is the safe direction: the caller turns an unknown estimate
    into the background path, which costs a little responsiveness. Guessing low
    would run a large job inside a request and freeze the worker.
    """
    plugin = _bare(plugin_module)
    assert plugin._estimate_scope_channels(
        CACHE, {"selected_groups": "US: News, US: Movies"}) is None


# --------------------------------------------------------------------------- #
# Exclude mode
# --------------------------------------------------------------------------- #
def test_exclude_drops_the_named_groups(plugin_module):
    plugin = _bare(plugin_module)
    kept = plugin._estimate_scope_channels(
        CACHE, {"selected_groups": "US: News", "group_filter_mode": "exclude"})
    assert [c["id"] for c in kept] == [3, 4]


def test_exclude_naming_an_unknown_group_is_not_a_problem(plugin_module):
    """Nothing to remove is a complete answer, unlike nothing to keep."""
    plugin = _bare(plugin_module)
    kept = plugin._estimate_scope_channels(
        CACHE, {"selected_groups": "US: Movies", "group_filter_mode": "exclude"})
    assert [c["id"] for c in kept] == [1, 2, 3, 4]


def test_excluding_everything_leaves_nothing(plugin_module):
    plugin = _bare(plugin_module)
    kept = plugin._estimate_scope_channels(
        CACHE, {"selected_groups": "US: News, US: Sports, UK: All",
                "group_filter_mode": "exclude"})
    assert kept == []


# --------------------------------------------------------------------------- #
# The mode comes from the live settings, the same resolver the actions use
# --------------------------------------------------------------------------- #
def test_an_unrecognised_mode_behaves_as_include(plugin_module):
    """Matching _resolve_group_filter_mode, so a stored value this build does not
    understand keeps a list of wanted groups meaning wanted."""
    plugin = _bare(plugin_module)
    kept = plugin._estimate_scope_channels(
        CACHE, {"selected_groups": "UK: All", "group_filter_mode": "sideways"})
    assert [c["id"] for c in kept] == [4]


# --------------------------------------------------------------------------- #
# The estimate must actually call it
# --------------------------------------------------------------------------- #
def test_the_estimate_narrows_before_counting(plugin_module):
    """Source-level, because the surrounding method needs more instance state
    than this harness can supply. It proves the call site exists."""
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    block = src[src.index("def _estimate_eta_seconds"):]
    block = block[:block.index("def run(self")]
    assert "_estimate_scope_channels(" in block
    assert block.index("_estimate_scope_channels(") < block.index("seen.add(")
