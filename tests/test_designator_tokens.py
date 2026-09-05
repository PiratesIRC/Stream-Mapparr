"""Channel designators like F1, E4 and M6 must survive matching intact.

Reported as issue #50 by v8eta on 2026-08-20 and reproduced exactly: on a UK
lineup, "Sky Sports F1 UHD" linked to "Sky Sports UHD 1", a different channel.

    Sky Sports F1 UHD  ->  Sky Sports F 1
    Sky Sports UHD 1   ->  Sky Sports 1
    calculate_similarity = 0.857, over the 0.85 default

WHY THE OBVIOUS FIX IS NOT ENOUGH. The report suggested requiring two letters
before the digit so single-letter tokens stop being split. Applied on its own to
a real copy of the matcher, the reported pair gets WORSE: 0.857 becomes 0.923,
because the similarity is character-level and deleting the space leaves the two
strings one character apart instead of two.

WHAT IS ACTUALLY WRONG. This plugin already has a numeric-sibling guard, which
refuses a candidate when the query has digit-only tokens and the candidate shares
none. It exists because FS1 against FS2 scores 96 percent. The letter-digit split
DEFEATS that guard by manufacturing the very token it checks:

    now          query "Sky Sports F 1" digits {1} | candidate "Sky Sports 1" digits {1}
                 they share 1, so the guard passes them

    keeping F1   query "Sky Sports F1"  digits {}  | candidate "Sky Sports 1" digits {1}
                 the guard never engages at all

So both halves are needed: keep the designator intact, AND teach the guard that a
letter-plus-digit token is a designator in its own right, so F1 against 1 is a
mismatch rather than an absence.

SCOPE. normalize_name lives in a matcher core that four plugins vendor byte for
byte, so it is not changed there. This plugin overrides it, which is what two of
the other three already do for their own reasons, and the vendored copy stays
hash-identical to the shared source.
"""
import pytest


@pytest.fixture
def fm(matcher):
    """A matcher with no channel databases loaded, at the default threshold."""
    return matcher(85)


# --------------------------------------------------------------------------- #
# The designator survives normalization
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw,expected_token", [
    ("Sky Sports F1 UHD", "f1"),
    ("E4", "e4"),
    ("E4 Extra", "e4"),
    ("M6", "m6"),
    ("i24NEWS", "i24"),
])
def test_a_single_letter_designator_is_not_torn_apart(fm, raw, expected_token):
    """The core splits a letter from a following digit unconditionally."""
    tokens = fm.normalize_name(raw).lower().split()
    assert expected_token in tokens, (raw, tokens)


def test_a_glued_multi_letter_word_is_still_split(fm):
    """The original reason the substitution exists must keep working."""
    assert "sports 1" in fm.normalize_name("BBC Sports1").lower()


def test_a_name_the_source_wrote_apart_is_never_joined(fm):
    """Only a split the core made is undone. Nothing else is joined.

    Measured on the live names without this restriction: "High Street TV 1"
    became "High Street TV1", "That's 60s" became "That's60 s" and
    "Titans at 49ers" became "Titans at49 ers", and the guard changed its
    decision on 8,945 candidate pairs instead of the 1,171 this is about.
    """
    assert "tv 1" in fm.normalize_name("High Street TV 1").lower()
    assert "at49" not in fm.normalize_name("Titans at 49ers").lower()
    assert "s60" not in fm.normalize_name("That's 60s").lower()

def test_only_the_glued_designator_is_rejoined_in_a_name_holding_both(fm):
    """A name can carry one of each, and only the glued one may be rejoined.

    "E4 TV 1" has E4 written glued and TV 1 written apart. The core splits both,
    and only the first may be put back. Without this case the membership test
    can be deleted and every other test still passes.
    """
    got = fm.normalize_name("E4 TV 1").lower()
    assert "e4" in got.split(), got
    assert "tv 1" in got, got

# --------------------------------------------------------------------------- #
# The guard, which is the half that actually stops the wrong match
# --------------------------------------------------------------------------- #
def test_the_reported_pair_no_longer_matches(fm):
    """The whole point. Sky Sports F1 is not Sky Sports 1."""
    got = fm.find_best_match("Sky Sports F1 UHD", ["Sky Sports UHD 1"])
    assert got in (None, (None, 0), ()) or not got or got[0] is None, got


def test_the_same_channel_still_matches_itself(fm):
    """A guard that blocks everything would also pass the test above."""
    got = fm.find_best_match("Sky Sports F1 UHD", ["Sky Sports F1 HD"])
    assert got and got[0] == "Sky Sports F1 HD", got


def test_numbered_siblings_are_still_kept_apart(fm):
    """The behaviour the guard already had must not be lost."""
    got = fm.find_best_match("Fox Sports 1", ["Fox Sports 2"])
    assert not got or got[0] is None, got


def test_a_numbered_channel_still_matches_its_own_number(fm):
    got = fm.find_best_match("Fox Sports 1", ["Fox Sports 1 HD"])
    assert got and got[0] == "Fox Sports 1 HD", got


def test_names_with_no_designator_are_untouched_by_the_guard(fm):
    """Most channels have no number at all and must be unaffected."""
    got = fm.find_best_match("CNN", ["CNN HD"])
    assert got and got[0] == "CNN HD", got


def test_a_designator_does_not_match_a_channel_that_has_none(fm):
    """Sky Sports F1 is not plain Sky Sports."""
    got = fm.find_best_match("Sky Sports F1", ["Sky Sports"])
    assert not got or got[0] is None, got


# --------------------------------------------------------------------------- #
# What counts as a designator
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("token,is_designator", [
    ("1", True), ("12", True),
    ("f1", True), ("e4", True), ("m6", True), ("tf1", True),
    ("sports", False), ("hd", False), ("news", False),
    ("", False), ("f", False),
])
def test_the_designator_test_accepts_only_short_alphanumeric_tokens(
        fuzzy_module, token, is_designator):
    assert fuzzy_module.is_designator_token(token) is is_designator
