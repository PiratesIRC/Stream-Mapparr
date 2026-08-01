"""Tests for the channel-group and stream-group filter MODE (include vs exclude).

The group settings were include-only: an empty list meant every group, and a
non-empty list meant only those groups. A user running a separate tool that owns
one static group (for example a group named "Teamarr") had no way to say "process
everything except this one" other than listing every other group by hand, which
silently stops processing any group created later.

A mode selector next to the existing comma-separated list turns the same list into
an exclusion list. The default is include, so an installation that never touches
the new setting behaves exactly as before.
"""


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --------------------------------------------------------------------------- #
# Mode resolution
# --------------------------------------------------------------------------- #

def test_absent_setting_resolves_to_include(plugin_module):
    """No stored value must mean the pre-existing behavior, not exclusion."""
    p = _bare(plugin_module)
    assert p._resolve_group_filter_mode({}) == "include"


def test_explicit_include_resolves_to_include(plugin_module):
    p = _bare(plugin_module)
    assert p._resolve_group_filter_mode({"group_filter_mode": "include"}) == "include"


def test_explicit_exclude_resolves_to_exclude(plugin_module):
    p = _bare(plugin_module)
    assert p._resolve_group_filter_mode({"group_filter_mode": "exclude"}) == "exclude"


def test_none_value_resolves_to_include(plugin_module):
    """A key present with the value None must not raise and must not flip the
    meaning of the list. dict.get cannot distinguish absent from present-but-None,
    and Dispatcharr never prunes a stored setting."""
    p = _bare(plugin_module)
    assert p._resolve_group_filter_mode({"group_filter_mode": None}) == "include"


def test_unrecognized_value_resolves_to_include(plugin_module):
    """An unknown value must fall back to the safe direction. Guessing exclude
    would turn a list of wanted groups into a list of skipped groups."""
    p = _bare(plugin_module)
    assert p._resolve_group_filter_mode({"group_filter_mode": "invert"}) == "include"


def test_mode_tolerates_surrounding_whitespace_and_case(plugin_module):
    p = _bare(plugin_module)
    assert p._resolve_group_filter_mode({"group_filter_mode": "  Exclude "}) == "exclude"


def test_stream_group_mode_is_independent_of_channel_group_mode(plugin_module):
    """Two separate settings: excluding a channel group must not also exclude the
    stream groups the user listed."""
    p = _bare(plugin_module)
    settings = {"group_filter_mode": "exclude", "stream_group_filter_mode": "include"}
    assert p._resolve_group_filter_mode(settings) == "exclude"
    assert p._resolve_stream_group_filter_mode(settings) == "include"


def test_stream_group_mode_defaults_to_include(plugin_module):
    p = _bare(plugin_module)
    assert p._resolve_stream_group_filter_mode({}) == "include"


# --------------------------------------------------------------------------- #
# Applying the filter
# --------------------------------------------------------------------------- #

def _channels():
    return [
        {"id": 1, "name": "CNN", "channel_group_id": 10},
        {"id": 2, "name": "ESPN", "channel_group_id": 20},
        {"id": 3, "name": "Teamarr Game 1", "channel_group_id": 30},
        {"id": 4, "name": "Ungrouped", "channel_group_id": None},
    ]


def test_include_keeps_only_the_listed_groups(plugin_module):
    p = _bare(plugin_module)
    kept = p._filter_by_group_ids(_channels(), [30], "include", "channel_group_id")
    assert [c["id"] for c in kept] == [3]


def test_exclude_drops_only_the_listed_groups(plugin_module):
    """The motivating case: one group owned by another tool is skipped and
    everything else is processed."""
    p = _bare(plugin_module)
    kept = p._filter_by_group_ids(_channels(), [30], "exclude", "channel_group_id")
    assert [c["id"] for c in kept] == [1, 2, 4]


def test_exclude_keeps_channels_that_have_no_group(plugin_module):
    """A channel with no group is not in the excluded set, so it must be
    processed. Include mode drops it, which is the existing behavior."""
    p = _bare(plugin_module)
    excluded = p._filter_by_group_ids(_channels(), [10], "exclude", "channel_group_id")
    included = p._filter_by_group_ids(_channels(), [10], "include", "channel_group_id")
    assert 4 in [c["id"] for c in excluded]
    assert 4 not in [c["id"] for c in included]


def test_exclude_every_group_leaves_nothing(plugin_module):
    p = _bare(plugin_module)
    kept = p._filter_by_group_ids(_channels(), [10, 20, 30], "exclude", "channel_group_id")
    assert [c["id"] for c in kept] == [4]


def test_filter_preserves_input_order(plugin_module):
    p = _bare(plugin_module)
    kept = p._filter_by_group_ids(_channels(), [20], "exclude", "channel_group_id")
    assert [c["id"] for c in kept] == [1, 3, 4]


def test_filter_works_on_streams_which_use_a_different_key(plugin_module):
    """Streams carry their group under 'channel_group', not 'channel_group_id'."""
    p = _bare(plugin_module)
    streams = [
        {"id": 1, "name": "CNN HD", "channel_group": 10},
        {"id": 2, "name": "Teamarr Feed", "channel_group": 30},
    ]
    kept = p._filter_by_group_ids(streams, [30], "exclude", "channel_group")
    assert [s["id"] for s in kept] == [1]


def test_unrecognized_mode_falls_back_to_include_when_filtering(plugin_module):
    """Belt and braces: even if a bad mode string reaches the filter directly, it
    must not silently invert the user's list."""
    p = _bare(plugin_module)
    kept = p._filter_by_group_ids(_channels(), [30], "invert", "channel_group_id")
    assert [c["id"] for c in kept] == [3]
