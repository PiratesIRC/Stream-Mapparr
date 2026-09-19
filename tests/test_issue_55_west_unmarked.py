"""Issue 55: an unmarked stream was attached to a WEST channel.

extract_zone returns DEFAULT for a stream name with no zone marker, and its
docstring says DEFAULT is the East feed in a US lineup. The channel side
already routes an unmarked channel as East. The stream side dropped only a
stream literally marked EAST from a West channel, so the same East feed was
dropped when the provider wrote "(EAST)" and kept when it did not. Measured on
the maintainer's box 2026-09-19: "STARZ (W)" held 6 unmarked rows and
"Showtime (W)" held 6, beside their real West feeds.

The rule now: a West channel keeps West streams only. When that leaves nothing,
Match and Assign leaves a channel that already holds streams untouched, and
gives the other-zone streams only to a channel holding none. Sort, which always
replaces the list, keeps the old keep-everything fallback.
"""

import inspect


class _Matcher:
    @staticmethod
    def extract_zone(name):
        upper = (name or "").upper()
        if "WEST" in upper or "PACIFIC" in upper or "(W)" in upper:
            return "WEST"
        if "EAST" in upper or "(E)" in upper:
            return "EAST"
        return "DEFAULT"


def _plugin(plugin_module, has_streams=None):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = _Matcher()
    if has_streams is not None:
        p._channel_has_streams = lambda channel_id: has_streams
    return p


def _s(name):
    return {"name": name}


def _names(streams):
    return [s["name"] for s in streams]


def _no_read(channel_id):
    raise AssertionError("the channel's rows were read when no decision needed them")


# The reporter's example: the same feed written two ways, plus the West feed.
UNMARKED = "INVESTIGATION CHANNEL"
MARKED_EAST = "INVESTIGATION CHANNEL (EAST)"
WEST = "INVESTIGATION CHANNEL WEST"


def test_the_reporters_case_the_unmarked_feed_leaves_the_west_channel(plugin_module):
    p = _plugin(plugin_module)
    out = p._order_streams_for_zone([_s(UNMARKED), _s(MARKED_EAST), _s(WEST)], "WEST")
    assert _names(out) == [WEST]


def test_the_unmarked_feed_stays_on_the_plain_channel(plugin_module):
    p = _plugin(plugin_module)
    out = p._order_streams_for_zone([_s(UNMARKED), _s(MARKED_EAST), _s(WEST)], "DEFAULT")
    assert _names(out) == [UNMARKED, MARKED_EAST]


def test_the_east_channel_is_unchanged(plugin_module):
    p = _plugin(plugin_module)
    out = p._order_streams_for_zone([_s(UNMARKED), _s(WEST), _s(MARKED_EAST)], "EAST")
    assert _names(out) == [UNMARKED, MARKED_EAST]


def test_without_a_west_stream_the_caller_can_ask_for_nothing(plugin_module):
    p = _plugin(plugin_module)
    out = p._order_streams_for_zone([_s(UNMARKED), _s(MARKED_EAST)], "WEST",
                                    keep_all_if_empty=False)
    assert out == []


def test_without_a_west_stream_the_default_still_keeps_everything(plugin_module):
    """Sort replaces a channel's rows with this list, so an empty list would
    take the channel off the air. The default keeps the other-zone streams in
    their input (quality) order, since unmarked and East now rank equal."""
    p = _plugin(plugin_module)
    out = p._order_streams_for_zone([_s(MARKED_EAST), _s(UNMARKED)], "WEST")
    assert _names(out) == [MARKED_EAST, UNMARKED]


def test_a_west_channel_that_is_not_zone_routed_is_untouched(plugin_module):
    """A lone marked channel is never zone-routed (brand words such as Key
    West), so the new rule cannot reach it."""
    p = _plugin(plugin_module)
    streams = [_s(UNMARKED), _s(WEST)]
    assert p._streams_for_channel(streams, 7, {}) is streams


# _streams_to_assign: what Match and Assign and Preview do with an empty result

def test_a_west_stream_present_is_assigned_without_a_database_read(plugin_module):
    p = _plugin(plugin_module)
    p._channel_has_streams = _no_read
    out, keep = p._streams_to_assign([_s(UNMARKED), _s(WEST)], 5, {5: "WEST"})
    assert (_names(out), keep) == ([WEST], False)


def test_no_west_stream_and_existing_streams_means_leave_it_alone(plugin_module):
    p = _plugin(plugin_module, has_streams=True)
    out, keep = p._streams_to_assign([_s(UNMARKED), _s(MARKED_EAST)], 5, {5: "WEST"})
    assert (out, keep) == ([], True)


def test_no_west_stream_and_an_empty_channel_gets_the_other_zone(plugin_module):
    """The choice there is the East feed or no feed; the 2026-08-02 rule
    chose the East feed, and it is logged."""
    p = _plugin(plugin_module, has_streams=False)
    out, keep = p._streams_to_assign([_s(UNMARKED), _s(MARKED_EAST)], 5, {5: "WEST"})
    assert (_names(out), keep) == ([UNMARKED, MARKED_EAST], False)


def test_nothing_matched_is_not_a_zone_decision(plugin_module):
    p = _plugin(plugin_module)
    p._channel_has_streams = _no_read
    assert p._streams_to_assign([], 5, {5: "WEST"}) == ([], False)


# Wiring: which action uses which path

def test_match_and_assign_skips_the_channel_before_writing(plugin_module):
    src = inspect.getsource(plugin_module.Plugin.add_streams_to_channels_action)
    assert "self._streams_to_assign(" in src
    assert "self._streams_for_channel(" not in src
    between = src.split("if keep_existing:", 1)[1].split("try:", 1)[0]
    assert "continue" in between
    assert "ChannelStream" not in between


def test_preview_uses_the_same_decision(plugin_module):
    src = inspect.getsource(plugin_module.Plugin.preview_changes_action)
    assert "self._streams_to_assign(" in src
    assert "self._streams_for_channel(" not in src
    assert "if keep_existing:" in src


def test_sort_keeps_the_keep_everything_fallback(plugin_module):
    """Sort deletes and recreates a channel's rows from this list, so it must
    never receive an empty one for a channel that has streams."""
    src = inspect.getsource(plugin_module.Plugin.sort_streams_action)
    assert "self._streams_for_channel(" in src
    assert "keep_all_if_empty=False" not in src
