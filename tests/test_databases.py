"""Schema validation for the *_channels.json country databases.

The US file alone is ~3.6 MB; a malformed hand-edit currently surfaces only at
runtime inside Dispatcharr. These tests catch structural breakage at commit/CI
time. The rules mirror what FuzzyMatcher._load_channel_databases actually relies
on, so a passing suite means the loader will not silently drop channels.
"""

import json

import pytest

from conftest import channel_db_files

DB_FILES = channel_db_files()
DB_IDS = [p.name for p in DB_FILES]

VALID_BROADCAST_MARKER = "broadcast"  # type contains this -> OTA channel (needs callsign)


@pytest.fixture(scope="module", params=DB_FILES, ids=DB_IDS)
def db(request):
    with open(request.param, encoding="utf-8") as f:
        data = json.load(f)  # raises -> test fails on invalid JSON
    return request.param.name, data


def test_has_channels_list(db):
    name, data = db
    assert isinstance(data, dict), f"{name}: top level must be an object"
    assert isinstance(data.get("channels"), list), f"{name}: missing 'channels' array"
    assert data["channels"], f"{name}: 'channels' is empty"


def test_country_code_present(db):
    name, data = db
    # Filename is the contract (US_channels.json -> US); the field should agree.
    code = data.get("country_code")
    assert code, f"{name}: missing 'country_code'"
    assert name.upper().startswith(str(code).upper()), \
        f"{name}: country_code {code!r} does not match filename"


def test_every_channel_has_type(db):
    name, data = db
    missing = [i for i, c in enumerate(data["channels"]) if not str(c.get("type", "")).strip()]
    assert not missing, f"{name}: {len(missing)} channel(s) missing 'type' (first idx {missing[:3]})"


def test_premium_channels_have_names(db):
    """Non-OTA channels must carry a non-empty channel_name (loader drops blanks)."""
    name, data = db
    bad = []
    for i, c in enumerate(data["channels"]):
        ctype = str(c.get("type", "")).lower()
        if VALID_BROADCAST_MARKER in ctype:
            continue
        if not str(c.get("channel_name", "")).strip():
            bad.append(i)
    assert not bad, f"{name}: {len(bad)} premium channel(s) with blank channel_name (first {bad[:3]})"


def test_broadcast_channels_have_callsigns(db):
    """OTA channels are matched purely by callsign — a blank one is unreachable."""
    name, data = db
    bad = []
    for i, c in enumerate(data["channels"]):
        if VALID_BROADCAST_MARKER in str(c.get("type", "")).lower():
            if not str(c.get("callsign", "")).strip():
                bad.append(i)
    assert not bad, f"{name}: {len(bad)} broadcast channel(s) missing 'callsign' (first {bad[:3]})"


def test_zones_field_is_nonempty_string_list(db):
    """If 'zones' is present it must be a list of non-empty strings (else _expand_zones warns/skips)."""
    name, data = db
    bad = []
    for i, c in enumerate(data["channels"]):
        if "zones" not in c:
            continue
        zones = c["zones"]
        if not isinstance(zones, list) or not zones or \
                any(not str(z).strip() for z in zones):
            bad.append((i, zones))
    assert not bad, f"{name}: malformed 'zones' on {len(bad)} channel(s): {bad[:3]}"


def test_at_least_one_database_present():
    """Guard against the glob silently finding nothing (e.g. wrong CWD in CI)."""
    assert DB_FILES, "no *_channels.json files found next to plugin.py"
