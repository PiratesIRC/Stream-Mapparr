"""East and West are only time zones in a country that has more than one.

The UK has a single time zone. "BBC ONE WEST" is one of the BBC's English
regions, sitting in a set with East, South, South East, South West, North West,
West Midlands, East Midlands, Yorkshire and London. It is not a time-shifted
feed and it must never be dropped from a channel as though it were.

Measured on a live installation 2026-08-02: the stream pool holds 36 distinct
BBC One regional names, and the plain "BBC One" channel had been given both
"UK: BBC ONE EAST" and "UK: BBC ONE WEST". Under a zone rule that knows nothing
about countries, one of those two is deleted and the other kept, which is
arbitrary because neither is a time shift.

So a name carrying a single-time-zone country marker is never zone-classified.
US and Canadian names are unaffected: those countries really do run East and
West feeds three hours apart, which is the whole reason the rule exists.
"""


def _z(fuzzy_module, name):
    return fuzzy_module.FuzzyMatcher.extract_zone(name)


# --------------------------------------------------------------------------- #
# Real UK regional names, all from the live stream pool
# --------------------------------------------------------------------------- #

UK_REGIONS = [
    "UK: BBC ONE WEST",
    "UK: BBC ONE EAST",
    "UK: BBC ONE SOUTH EAST",
    "UK: BBC ONE SOUTH WEST",
    "UK: BBC ONE NORTH WEST",
    "UK: BBC ONE WEST MIDLANDS",
    "UK: BBC ONE EAST MIDLANDS",
    "UK: BBC ONE NORTH EAST & CUMBRIA",
    "UK: BBC ONE EAST YORKSHIRE & LINCOLNSHIRE",
]


def test_a_uk_regional_feed_is_never_a_time_zone(fuzzy_module):
    for name in UK_REGIONS:
        assert _z(fuzzy_module, name) == "DEFAULT", name


def test_the_uk_rule_survives_a_trailing_decorator(fuzzy_module):
    """Providers append superscripts and symbols to these names."""
    assert _z(fuzzy_module, "UK: BBC ONE WEST ᴿᴬᵂ") == "DEFAULT"
    assert _z(fuzzy_module, "UK: BBC ONE WEST ◉") == "DEFAULT"


def test_other_single_timezone_countries_are_covered(fuzzy_module):
    for name in ("DE: WDR WEST", "FR: France 3 Ouest EAST", "IE: RTE ONE WEST",
                 "NL: NPO WEST", "ES: TVE EAST", "IT: RAI WEST"):
        assert _z(fuzzy_module, name) == "DEFAULT", name


# --------------------------------------------------------------------------- #
# Countries that really do run time-shifted feeds
# --------------------------------------------------------------------------- #

def test_a_us_feed_is_still_zoned(fuzzy_module):
    for name, want in [("US: ANIMAL PLANET WEST HD", "WEST"),
                       ("US: STARZ WEST HD", "WEST"),
                       ("US: CINEMAX EAST HD", "EAST"),
                       ("GO: HBO EAST", "EAST"),
                       ("PRIME: HBO WEST", "WEST")]:
        assert _z(fuzzy_module, name) == want, name


def test_a_canadian_feed_is_still_zoned(fuzzy_module):
    """Canada spans six time zones and does run East and West feeds."""
    assert _z(fuzzy_module, "CA: CTV WEST") == "WEST"


def test_an_unprefixed_name_is_still_zoned(fuzzy_module):
    """Most of the lineup carries no country marker at all. Those keep the
    existing behaviour rather than being exempted by accident."""
    assert _z(fuzzy_module, "Showtime (W)") == "WEST"
    assert _z(fuzzy_module, "HBO Pacific") == "WEST"
    assert _z(fuzzy_module, "Discovery Channel West") == "WEST"


def test_the_place_name_rule_still_applies(fuzzy_module):
    """Both guards hold at once."""
    assert _z(fuzzy_module, "US: ABC 25 (WPBF) West Palm Beach HD") == "DEFAULT"
