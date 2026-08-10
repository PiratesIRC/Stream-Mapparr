"""The token gate must not reject a stream whose name differs only in where the
word breaks fall.

THE FAULT. Normalisation splits a name at an internal capital, so the channel
"UnXplained Zone" becomes the tokens un, xplained and zone. A provider writing
the same channel in upper case as "UNXPLAINED ZONE" yields unxplained and zone,
because there is no capital left to split on. Before the similarity comparison
runs, a fast gate asks whether every channel token appears in the stream's
tokens. At a match threshold of 90 or above that subset test is the only route
through, it fails, and the comparison never happens, so an exact match is
discarded.

It runs in the other direction too, where the STREAM is the split side: the
channel "Nicktoons" against a stream named "NICK TOONS".

MEASURED on a live installation of 1,440 channels and 25,323 streams: 17
channels affected and 134 newly eligible pairs. Two of them, EuroNews and
WildEarth, had been reported as matching nothing at all.

WHAT THIS MUST NOT DO. The strict gate exists to stop "Premier Sports 1"
matching "Premier Sports 2". Removing the spaces keeps every other character, so
only names spelled identically can pass; a partial overlap, a missing word or an
extra one still fails.

THE ORDERING TRAP, found the hard way. A first attempt compared the token sets
by joining them in SORTED order. That only works when alphabetical order happens
to match word order: IndiePlex and MoviePlex passed by that coincidence while
RetroPlex, whose "plex" sorts before "retro", did not. Comparing the normalised
STRINGS instead preserves word order and has no such accident in it.

Admitting a pair is NOT matching it. The similarity threshold, the country
restriction and the callsign rules all still apply afterwards. Verified on the
live installation: with the gate widened, "NBC - DC Washington (WZDC)" still
matches only its own WZDC stream and does not pick up the WRC one, and the
British streams newly eligible for the US channels Lifetime and Nicktoons are
still dropped as foreign.
"""
import pytest


# --------------------------------------------------------------------------- #
# The second obstacle: the similarity score
# --------------------------------------------------------------------------- #

def _breaks(plugin_module, left, right):
    return plugin_module._same_but_for_word_breaks(left, right)


def test_a_missing_space_is_treated_as_the_same_name(plugin_module):
    """Widening the token gate alone fixed NOTHING, which is how this was found.
    The pair reached the similarity comparison and the single missing space cost
    enough score to fail: 0.938 measured for this pair against a threshold of
    95, 0.947 for magellan tv wildest, 0.889 for euro news."""
    assert _breaks(plugin_module, "un xplained zone", "unxplained zone")
    assert _breaks(plugin_module, "magellan tv wildest", "magellantv wildest")
    assert _breaks(plugin_module, "euro news", "euronews")


def test_it_is_not_a_fuzzy_comparison(plugin_module):
    """Removing spaces keeps every other character, so only names spelled
    identically can pass. This must not become a back door around the
    threshold."""
    assert not _breaks(plugin_module, "premier sports 1", "premier sports 2")
    assert not _breaks(plugin_module, "game show", "game show central")
    assert not _breaks(plugin_module, "discovery science", "discovery turbo")
    assert not _breaks(plugin_module, "amc", "bbc")


def test_an_empty_side_matches_nothing(plugin_module):
    assert not _breaks(plugin_module, "", "amc")
    assert not _breaks(plugin_module, "amc", "")
    assert not _breaks(plugin_module, "   ", "")


def test_every_place_that_can_reject_the_pair_consults_it(plugin_module):
    """THREE call sites, not two: the two places a similarity score is tested
    against the threshold, plus the token gate that runs before them. Fixing
    some and not others fixes a channel on one code path and not another, which
    is how the first attempt at this shipped looking correct and changed
    nothing."""
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    assert src.count("_same_but_for_word_breaks(stream_lower, channel_lower)") == 3


# --------------------------------------------------------------------------- #
# The ordering trap
# --------------------------------------------------------------------------- #

def test_word_order_is_preserved(plugin_module):
    """The case that exposed the flaw. A first attempt joined the token sets in
    SORTED order, which only works when alphabetical order matches word order.
    IndiePlex and MoviePlex passed by coincidence; RetroPlex did not, because
    "plex" sorts before "retro". All three must pass."""
    assert _breaks(plugin_module, "indie plex", "indieplex")
    assert _breaks(plugin_module, "movie plex", "movieplex")
    assert _breaks(plugin_module, "retro plex", "retroplex")


def test_a_reordering_is_not_a_match(plugin_module):
    """The other half of preserving order: the same words in a different order
    are a different name, and must not be admitted."""
    assert not _breaks(plugin_module, "plex retro", "retroplex")
    assert not _breaks(plugin_module, "news sky", "skynews")


def test_the_gate_uses_the_string_comparison_not_a_token_join(plugin_module):
    """Source-level. A token join cannot preserve word order, so reintroducing
    one would bring the same accident back."""
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    assert "_tokens_agree_when_joined" not in src
    assert src.count("_same_but_for_word_breaks(stream_lower, channel_lower)") == 3
