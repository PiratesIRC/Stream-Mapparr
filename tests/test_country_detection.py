"""Unit tests for country.py (bug-158). Pure module: no Django, no plugin import."""

import pytest

from country import SAME, FOREIGN, UNKNOWN, classify, country_from_group, country_from_name


# --- country_from_name: anchored prefix forms ------------------------------ #

@pytest.mark.parametrize("name,expected", [
    # whole-string (rev 1 missed this entirely — the reporter's group "US")
    ("US", "US"),
    ("USA", "US"),
    ("uk", "UK"),
    # token + delimiter, including the "|" this box's groups use
    ("US: CNN", "US"),
    ("US| PRIME RAW", "US"),
    ("US : TNT FHD", "US"),
    ("US | TNT HD", "US"),
    ("MEX: TNT", "MX"),
    ("ARG: CNN", "AR"),
    ("PT: CNN", "PT"),
    ("UK-Sky News", "UK"),
    # matched-delimiter wrap
    ("(US) ABC", "US"),
    ("[UK] BBC One", "UK"),
    ("|CA| TSN", "CA"),
    # multi-token prefix (the reporter's "CA LBW:")
    ("CA LBW: CNN", "CA"),
    ("CA LBW: USA NETWORK", "CA"),
    # country glued to a quality tag
    ("UKSD: Sky Sports", "UK"),
    ("USFHD ESPN", "US"),
    # bare code + whitespace
    ("US CNN", "US"),
    ("CA TSN 1 HD", "CA"),
    # long-form country words anywhere in the name
    ("CNN Brasil", "BR"),
    ("Sky News Australia", "AU"),
])
def test_country_from_name_detects(name, expected):
    assert country_from_name(name) == expected


@pytest.mark.parametrize("name", [
    # brand words that must NOT self-classify
    "USA Network",
    "USA Network FHD",
    "CNN USA",
    "IN Country Television",
    "IT Crowd",
    "NO Reservations",
    "Sky Nature HD",
    # FAST distribution-platform tags: NOT countries. 939 of these in the real
    # corpus; treating them as foreign dropped 121 channels to zero (bug-159).
    "GO: ESPN",
    "RK: DAZN RINGSIDE",
    "TUBI: Cheers",
    # affiliate-network and bouquet prefixes
    "FOX: EL PASO TX KFOX",
    "CBS: FL ORLANDO WKMG",
    "NFL TEAMS: CBS Panthers",
    "PPV: Main Event",
    # quality/variant tags occupying the country slot
    "LBW: CNN",
    "FHD: CNN",
    # multi-token branch must not read an English word as a country
    "IN PROGRESS: Something",
    "NO LIMIT TV: Something",
    "IT SPORTS: Something",
    # unrecognized, ambiguous
    "DR: TNT",
    "",
    None,
])
def test_country_from_name_returns_none(name):
    assert country_from_name(name) is None


# --- country_from_group: wider alias scan ---------------------------------- #

@pytest.mark.parametrize("group,expected", [
    ("US| PRIME RAW", "US"),
    ("US| ROKU RAW", "US"),
    ("UK| GENERAL HD/RAW", "UK"),
    ("US", "US"),
    ("USA: News", "US"),
    ("USA Networks", "US"),
    ("UNITED STATES", "US"),
    ("Canada HD", "CA"),
    ("GREAT BRITAIN", "UK"),
])
def test_country_from_group_detects(group, expected):
    assert country_from_group(group) == expected


@pytest.mark.parametrize("group", ["News", "Entertainment", "Sports", "", None])
def test_country_from_group_returns_none(group):
    assert country_from_group(group) is None


# --- classify -------------------------------------------------------------- #

def test_classify_same():
    assert classify("US", "US") is SAME


def test_classify_foreign():
    assert classify("US", "UK") is FOREIGN
    assert classify("US", "AR") is FOREIGN


def test_classify_unknown_stream_is_unknown_not_foreign():
    """bug-159: an unrecognized stream country is UNKNOWN and must be KEPT.
    Treating it as FOREIGN dropped 924/2439 real matches."""
    assert classify("US", None) is UNKNOWN


def test_classify_unknown_channel_is_unknown():
    assert classify(None, "UK") is UNKNOWN
