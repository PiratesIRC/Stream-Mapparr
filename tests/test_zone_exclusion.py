"""Opposite-zone feeds are not attached at all, rather than attached last.

A plain-named channel such as "HBO" is its East or national feed. Before this
change it still received every HBO West stream, ordered below the East ones as
failover alternates. That only shows up when every East feed fails: Dispatcharr
walks down the list, reaches HBO West, and plays a film three hours behind. For
a movie channel that is a different programme, not a brief glitch.

So a channel now receives its OWN zone and unmarked feeds only. The opposite
zone is dropped.

One deliberate exception, in _order_streams_for_zone: if dropping would leave a
channel with NO streams at all, the original list is kept and a warning is
logged. Match and Assign REPLACES a channel's whole stream list, so emptying it
would take the channel off the air entirely, which is worse than the problem
being solved and is a data-loss shape rather than an ordering one.
"""


class _Matcher:
    """Minimal stand-in for the zone extractor the real matcher provides."""

    @staticmethod
    def extract_zone(name):
        upper = (name or "").upper()
        if "WEST" in upper or "PACIFIC" in upper or "(W)" in upper:
            return "WEST"
        if "EAST" in upper or "(E)" in upper:
            return "EAST"
        return "DEFAULT"


class _Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, msg, *a, **k):
        self.warnings.append(str(msg))

    info = debug = error = lambda self, *a, **k: None


def _plugin(plugin_module, logger=None):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = _Matcher()
    if logger is not None:
        p._zone_logger = logger
    return p


def _s(name):
    return {"name": name}


# --------------------------------------------------------------------------- #
# A plain or East channel keeps East and unmarked, drops West
# --------------------------------------------------------------------------- #

def test_a_plain_channel_drops_west_streams(plugin_module):
    p = _plugin(plugin_module)
    streams = [_s("GO: HBO EAST"), _s("US: HBO HD"), _s("PRIME: HBO WEST"),
               _s("US: HBO WEST HD")]
    out = p._order_streams_for_zone(streams, "DEFAULT")
    names = [s["name"] for s in out]
    assert names == ["GO: HBO EAST", "US: HBO HD"]


def test_a_plain_channel_drops_pacific_streams(plugin_module):
    """Pacific folds into West: a US premium channel's West feed IS its
    Pacific-time feed."""
    p = _plugin(plugin_module)
    streams = [_s("US: HBO HD"), _s("US: HBO PACIFIC")]
    assert [s["name"] for s in p._order_streams_for_zone(streams, "DEFAULT")] == ["US: HBO HD"]


def test_an_east_channel_drops_west_streams(plugin_module):
    p = _plugin(plugin_module)
    streams = [_s("SHOWTIME EAST"), _s("SHOWTIME WEST")]
    assert [s["name"] for s in p._order_streams_for_zone(streams, "EAST")] == ["SHOWTIME EAST"]


# --------------------------------------------------------------------------- #
# A West channel is treated symmetrically
# --------------------------------------------------------------------------- #

def test_a_west_channel_drops_east_streams_but_keeps_unmarked(plugin_module):
    """An East feed on a West channel is wrong in the same way, three hours the
    other direction. Unmarked feeds are kept for both, so neither is left with
    nothing when only generic feeds exist."""
    p = _plugin(plugin_module)
    streams = [_s("STARZ WEST HD"), _s("US: STARZ HD"), _s("US: STARZ EAST HD")]
    out = [s["name"] for s in p._order_streams_for_zone(streams, "WEST")]
    assert "US: STARZ EAST HD" not in out
    assert out == ["STARZ WEST HD", "US: STARZ HD"]


# --------------------------------------------------------------------------- #
# Order within what survives is unchanged
# --------------------------------------------------------------------------- #

def test_own_zone_still_ranks_above_unmarked(plugin_module):
    p = _plugin(plugin_module)
    streams = [_s("US: STARZ HD"), _s("STARZ WEST HD")]
    assert [s["name"] for s in p._order_streams_for_zone(streams, "WEST")] == [
        "STARZ WEST HD", "US: STARZ HD"]


def test_the_country_partition_still_outranks_zone(plugin_module):
    """Same-country first stays the outer sort key."""
    p = _plugin(plugin_module)
    generic, east = _s("HBO HD"), _s("HBO EAST")
    out = p._order_streams_for_zone([generic, east], "DEFAULT",
                                    same_country_ids={id(east)})
    assert out[0] is east


# --------------------------------------------------------------------------- #
# The exception: never empty a channel
# --------------------------------------------------------------------------- #

def test_dropping_is_skipped_when_it_would_leave_no_streams(plugin_module):
    """Match and Assign replaces a channel's whole stream list, so emptying it
    takes the channel off the air. Keeping the wrong-zone feed is the lesser
    harm, and it is logged rather than done silently."""
    p = _plugin(plugin_module)
    streams = [_s("HBO WEST"), _s("HBO PACIFIC")]
    out = p._order_streams_for_zone(streams, "DEFAULT")
    assert [s["name"] for s in out] == ["HBO WEST", "HBO PACIFIC"]


def test_an_empty_input_stays_empty(plugin_module):
    p = _plugin(plugin_module)
    assert p._order_streams_for_zone([], "DEFAULT") == []


def test_no_matcher_means_no_change(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None
    streams = [_s("HBO EAST"), _s("HBO WEST")]
    assert p._order_streams_for_zone(streams, "DEFAULT") == streams
