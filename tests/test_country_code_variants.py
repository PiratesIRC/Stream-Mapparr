"""Providers disagree about which code a country gets, so both forms resolve.

Reported by a user: some providers prefix United Kingdom channels with the
official ISO code GB and others with the common but unofficial UK. The same
split exists for the Dominican Republic, where some use the official DO and
others use DR.

Measured before this change: UK and GBR both resolved to the United Kingdom,
while GB did not. DO and DOM both resolved to the Dominican Republic, while DR
did not.

The practical effect was smaller than it sounds, because the country filter
removes only streams it can prove are foreign. An unrecognised prefix yields
None, which is treated as unknown and kept, so nothing was ever dropped
wrongly. What was lost is the same-country preference: a stream prefixed GB was
not recognised as British and did not get that benefit.

Both variants map to the code the shipped channel databases already use, so
UK_channels.json continues to serve GB-prefixed feeds with no duplicate file.
That is the answer to the other half of the report: a database is selected by
the operator, never by the provider's prefix, so no database needs duplicating.
"""

import country


def test_the_reported_united_kingdom_variants_both_resolve():
    for name in ("UK: BBC One", "GB: BBC One", "GBR: BBC One"):
        assert country.country_from_name(name) == "UK", name


def test_the_reported_dominican_republic_variants_both_resolve():
    for name in ("DO: Telemicro", "DR: Telemicro", "DOM: Telemicro"):
        assert country.country_from_name(name) == "DO", name


def test_both_variants_map_onto_the_shipped_database_code():
    """UK_channels.json is keyed "UK", so GB must resolve to UK and not to a
    third code, or the country filter and the database would disagree."""
    assert country.country_from_name("GB: ITV") == "UK"
    assert "GB" not in country.KNOWN_COUNTRY_CODES or \
        country.country_from_name("GB: ITV") == "UK"


def test_the_variants_work_in_a_group_name_too():
    """Country can be read from a channel group as well as a stream name."""
    for value, want in (("GB", "UK"), ("DR", "DO")):
        assert country.country_from_group(value) == want, value


def test_case_does_not_matter_for_the_new_variants():
    for name, want in (("gb: bbc one", "UK"), ("Gb: BBC One", "UK"),
                       ("dr: telemicro", "DO")):
        assert country.country_from_name(name) == want, name


# --------------------------------------------------------------------------- #
# What must not change
# --------------------------------------------------------------------------- #

def test_the_existing_codes_are_untouched():
    for name, want in (("US: CNN", "US"), ("USA: CNN", "US"),
                       ("CA: CTV", "CA"), ("DE: ARD", "DE"),
                       ("FR: TF1", "FR"), ("AU: Nine", "AU")):
        assert country.country_from_name(name) == want, name


def test_a_platform_tag_is_still_not_a_country():
    """GO and RK are streaming platform tags on US channels, not countries.
    Treating one as a country would drop hundreds of real matches."""
    for name in ("GO: HBO EAST", "RK: BRAVO VAULT", "PLUTO: Comedy"):
        assert country.country_from_name(name) is None, name


def test_an_unknown_prefix_still_yields_none_rather_than_guessing():
    """Fail open. An unrecognised prefix must stay unknown so the stream is
    kept, rather than being guessed into a country and possibly filtered out."""
    for name in ("ZZ: Something", "QQQ: Something"):
        assert country.country_from_name(name) is None, name


def test_a_quality_tag_in_the_country_slot_is_still_not_a_country():
    for name in ("HD: Sky One", "4K: Sky One", "RAW: Sky One"):
        assert country.country_from_name(name) is None, name


# --------------------------------------------------------------------------- #
# The deliberate limit of the synonym, and why it is drawn here
# --------------------------------------------------------------------------- #

def test_the_danish_broadcaster_is_not_read_as_the_dominican_republic():
    """DR is also Denmark's public broadcaster, whose channels are DR1, DR2 and
    DR3. Those must never be read as a Dominican Republic country marker.

    This holds because the synonyms are deliberately NOT added to the curated
    code set. The bare and space separated forms only accept curated codes, so
    a name beginning "DR " is not a country marker, while the explicit
    delimiter form "DR:" is. That explicit form is the one the reporting user
    described their provider using, so the narrow rule covers the report
    without touching Danish channel names."""
    for name in ("DR1", "DR1 HD", "DR 1 HD", "DR2 Denmark", "DR TV", "DR P3"):
        assert country.country_from_name(name) is None, name


def test_a_danish_prefix_still_resolves_to_denmark():
    assert country.country_from_name("DK: DR1") == "DK"


def test_only_the_explicit_prefix_form_resolves_a_synonym():
    """Stated as a test so the limit is not mistaken for an oversight and
    quietly widened later."""
    assert country.country_from_name("DR: Telemicro") == "DO"
    assert country.country_from_name("DR Telemicro") is None
    assert country.country_from_name("GB: BBC One") == "UK"
    assert country.country_from_name("GB BBC One") is None
