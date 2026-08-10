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
matching "Premier Sports 2". Joining the sorted tokens keeps every letter and
every token, so it can only accept names built from the same words; a partial
overlap, a missing word or an extra one still fails.

Admitting a pair is NOT matching it. The similarity threshold, the country
restriction and the callsign rules all still apply afterwards. Verified on the
live installation: with the gate widened, "NBC - DC Washington (WZDC)" still
matches only its own WZDC stream and does not pick up the WRC one, and the
British streams newly eligible for the US channels Lifetime and Nicktoons are
still dropped as foreign.
"""
import pytest


def _agree(plugin_module, channel, stream):
    return plugin_module._tokens_agree_when_joined(set(channel), set(stream))


# --------------------------------------------------------------------------- #
# The real cases, taken from live data
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("channel_tokens,stream_tokens,label", [
    ({"un", "xplained", "zone"}, {"unxplained", "zone"}, "UnXplained Zone"),
    ({"magellan", "tv", "wildest"}, {"magellantv", "wildest"}, "MagellanTV Wildest"),
    ({"indie", "plex"}, {"indieplex"}, "IndiePlex"),
    ({"movie", "sphere"}, {"moviesphere"}, "MovieSphere"),
    ({"accu", "weather"}, {"accuweather"}, "AccuWeather"),
    ({"golf", "pass"}, {"golfpass"}, "GolfPass"),
    ({"draft", "kings"}, {"draftkings"}, "DraftKings"),
    ({"euro", "news"}, {"euronews"}, "EuroNews"),
])
def test_a_word_break_difference_is_admitted(plugin_module, channel_tokens,
                                             stream_tokens, label):
    assert _agree(plugin_module, channel_tokens, stream_tokens), label


def test_it_works_when_the_stream_is_the_split_side(plugin_module):
    """The channel Nicktoons against a stream named NICK TOONS."""
    assert _agree(plugin_module, {"nicktoons"}, {"nick", "toons"})


# --------------------------------------------------------------------------- #
# What it must still reject
# --------------------------------------------------------------------------- #

def test_a_numbered_sibling_is_still_rejected(plugin_module):
    """The exact false match the strict gate was written to prevent."""
    assert not _agree(plugin_module, {"premier", "sports", "1"},
                      {"premier", "sports", "2"})


def test_an_extra_word_is_still_rejected(plugin_module):
    """Game Show against Game Show Central: a real pair from live data that
    must NOT be admitted, because they are different channels."""
    assert not _agree(plugin_module, {"game", "show"},
                      {"game", "show", "central"})


def test_a_missing_word_is_still_rejected(plugin_module):
    assert not _agree(plugin_module, {"sky", "sports", "news"}, {"sky", "sports"})


def test_a_partial_overlap_is_still_rejected(plugin_module):
    assert not _agree(plugin_module, {"discovery", "science"},
                      {"discovery", "turbo"})


def test_an_unrelated_name_is_rejected(plugin_module):
    assert not _agree(plugin_module, {"amc"}, {"bbc"})


def test_an_empty_side_is_rejected(plugin_module):
    """A name that normalises to nothing must not match everything."""
    assert not _agree(plugin_module, set(), {"amc"})
    assert not _agree(plugin_module, {"amc"}, set())


def test_identical_token_sets_are_admitted(plugin_module):
    assert _agree(plugin_module, {"amc"}, {"amc"})


# --------------------------------------------------------------------------- #
# The gate call site
# --------------------------------------------------------------------------- #

def test_the_matching_loop_consults_the_helper(plugin_module):
    """Source-level, because the surrounding loop needs the ORM. It proves the
    call site exists, not that it runs; the live before-and-after measurement
    recorded in this module's docstring covers that."""
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    assert "_tokens_agree_when_joined(channel_tokens, stream_tokens)" in src
