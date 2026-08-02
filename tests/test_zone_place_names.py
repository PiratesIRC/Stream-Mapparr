"""A place name containing "West" or "East" is not a time zone.

This mattered little while zone detection only REORDERED a channel's streams: a
misread name was ranked oddly and nothing was lost. Once opposite-zone streams
are dropped instead, a misread name deletes a working assignment.

Every string below is a real channel or stream name taken from a live
installation on 2026-08-02, where 24 of the 191 assignments the exclusion rule
would have removed were place names rather than feeds. Two channels would have
been left with no streams at all.

The rule: a zone marker stands alone. It is parenthesised, or the last word, or
followed only by a quality or format tag. A marker followed by another WORD is
part of a place name.
"""


def _extract(fuzzy_module, name):
    return fuzzy_module.FuzzyMatcher.extract_zone(name)


# --------------------------------------------------------------------------- #
# Real place names that must NOT read as a zone
# --------------------------------------------------------------------------- #

PLACE_NAMES = [
    "US: ABC 25 (WPBF) West Palm Beach HD",
    "US: NBC (KSNB) WEST HASTINGS, NE (D)",
    "US: NBC (WVIT) WEST HARTFORD, CT (D)",
    "US: FOX 6 (WITI) West Allis HD",
    "Key West Community TV",
    "US: East Tennessee PBS HD",
    "West Virginia Public Broadcasting",
    "EastEnders Classics",
]


def test_a_place_name_is_not_a_zone(fuzzy_module):
    for name in PLACE_NAMES:
        assert _extract(fuzzy_module, name) == "DEFAULT", name


# --------------------------------------------------------------------------- #
# Real zone markers that must STILL be detected
# --------------------------------------------------------------------------- #

WEST_FEEDS = [
    "US: ANIMAL PLANET WEST HD",
    "US: BRAVO WEST HD",
    "US: CARTOON NETWORK WEST HD",
    "US: DISCOVERY WEST HD",
    "PRIME: HBO WEST",
    "US: STARZ WEST HD",
    "GO: STARZ ENCORE WEST",
    "Showtime (W)",
    "STARZ (W)",
    "Cinemax (WEST)",
    "US: Magnolia Network West",
    "HBO Pacific",
    "Cinemax (Pacific)",
    "Showtime (PT)",
]

EAST_FEEDS = [
    "US: CINEMAX EAST HD",
    "GO: HBO EAST",
    "US: STARZ ENCORE EAST HD",
    "Showtime (E)",
    "HBO Drama HD East",
    "Cinemax (EAST)",
]


def test_a_real_west_feed_is_still_detected(fuzzy_module):
    for name in WEST_FEEDS:
        assert _extract(fuzzy_module, name) == "WEST", name


def test_a_real_east_feed_is_still_detected(fuzzy_module):
    for name in EAST_FEEDS:
        assert _extract(fuzzy_module, name) == "EAST", name


# --------------------------------------------------------------------------- #
# The rule itself
# --------------------------------------------------------------------------- #

def test_a_marker_as_the_last_word_is_a_zone(fuzzy_module):
    assert _extract(fuzzy_module, "Discovery Channel West") == "WEST"
    assert _extract(fuzzy_module, "Discovery Channel East") == "EAST"


def test_a_marker_followed_only_by_a_quality_tag_is_a_zone(fuzzy_module):
    for tag in ("HD", "SD", "FHD", "UHD", "4K", "HEVC", "H265", "RAW"):
        assert _extract(fuzzy_module, f"Discovery West {tag}") == "WEST", tag


def test_a_marker_followed_by_another_word_is_a_place(fuzzy_module):
    assert _extract(fuzzy_module, "Discovery West Midlands") == "DEFAULT"
    assert _extract(fuzzy_module, "News East Anglia") == "DEFAULT"


def test_a_parenthesised_marker_is_a_zone_wherever_it_sits(fuzzy_module):
    assert _extract(fuzzy_module, "Showtime (W) HD Backup") == "WEST"
    assert _extract(fuzzy_module, "Showtime (EAST) Feed 2") == "EAST"


def test_a_bare_single_letter_is_still_not_a_zone(fuzzy_module):
    """Unchanged: too many false positives, for example the UK channel W and
    E! Entertainment."""
    assert _extract(fuzzy_module, "W") == "DEFAULT"
    assert _extract(fuzzy_module, "E! Entertainment Television") == "DEFAULT"


def test_an_unmarked_name_is_default(fuzzy_module):
    assert _extract(fuzzy_module, "HBO") == "DEFAULT"
    assert _extract(fuzzy_module, "") == "DEFAULT"
    assert _extract(fuzzy_module, None) == "DEFAULT"


def test_a_trailing_decorator_does_not_hide_the_marker(fuzzy_module):
    """Providers append superscript and symbol decorations. Those must not stop
    a real marker being seen as the last meaningful word.

    The example used to be "UK: BBC ONE WEST", which is no longer a zone at all:
    the UK has one time zone, so its East and West are regions. See
    tests/test_zone_single_timezone.py. A US name is used instead, where the
    marker really is a time-shifted feed."""
    assert _extract(fuzzy_module, "US: ANIMAL PLANET WEST ᴿᴬᵂ") == "WEST"
    assert _extract(fuzzy_module, "US: STARZ WEST ◉") == "WEST"
