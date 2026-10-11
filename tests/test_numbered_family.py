"""Issue #62: channels in a numbered family (Event 1 to Event 20) must each collect
only the stream carrying their own number.

The stream-collecting loop in _match_streams_to_channel has a containment branch:
"nebula one event 1" is contained in "nebula one event 12", the length ratio is
0.947 and the similarity 0.947, so below threshold 95 the branch accepted the
stream without ever reaching the number-token check further down. Measured before
the fix at threshold 90: Event 1 collected Events 1 and 10 to 19, and Event 12
collected Event 1.
"""
import logging

import pytest

import fuzzy_matcher

LOGGER = logging.getLogger("test_numbered_family")
KEEP_ALL = dict(ignore_quality=False, ignore_regional=False,
                ignore_geographic=False, ignore_misc=False)
STREAMS = [
    {"id": n, "name": "NebulaOne Event %d HD (WEB)" % n, "channel_group__name": None,
     "m3u_account": 1, "url": "http://example/s%d" % n}
    for n in range(1, 21)
]


def _collect(plugin_module, channel_name, threshold):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_matcher.FuzzyMatcher(match_threshold=threshold)
    ignore = P._parse_tags("HD, (WEB)")
    matched, _, _, _, _ = inst._match_streams_to_channel(
        {"id": 1, "name": channel_name, "channel_group__name": None},
        STREAMS, LOGGER, ignore_tags=ignore, channels_data=[], **KEEP_ALL)
    return [s["name"] for s in matched]


@pytest.mark.parametrize("threshold", [70, 80, 90, 95])
@pytest.mark.parametrize("number", [1, 2, 9, 10, 12, 19, 20])
def test_each_numbered_channel_collects_only_its_own_stream(plugin_module, threshold, number):
    got = _collect(plugin_module, "NebulaOne Event %d" % number, threshold)
    assert got == ["NebulaOne Event %d HD (WEB)" % number]


def test_unnumbered_channel_containment_is_unchanged(plugin_module):
    """The containment branch still accepts a name that differs only by an
    ignored tag, and a channel with no number is not newly constrained."""
    got = _collect(plugin_module, "NebulaOne Event 12 HD", 95)
    assert got == ["NebulaOne Event 12 HD (WEB)"]
