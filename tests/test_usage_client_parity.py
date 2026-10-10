"""The vendored usage client must never drift from its pin.

Stream-Mapparr/usage_client.py is produced by plugin-stats/client/vendor.py and pinned in
scripts/client_manifest.json. Change it by editing plugin-stats/client/usage_client.py,
running its tests and re-copying with vendor.py; never patch the copy in place.
"""
import hashlib
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDORED = ROOT / "Stream-Mapparr" / "usage_client.py"
MANIFEST = ROOT / "scripts" / "client_manifest.json"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_vendored_usage_client_exists():
    assert VENDORED.is_file(), "Stream-Mapparr/usage_client.py is missing; copy it with plugin-stats/client/vendor.py"


def test_the_vendored_usage_client_matches_its_pin():
    pinned = json.loads(MANIFEST.read_text(encoding="utf-8"))["usage_client.py"]
    assert _sha256(VENDORED) == pinned


def test_the_vendored_usage_client_matches_the_source_when_present():
    source = ROOT.parent / "plugin-stats" / "client" / "usage_client.py"
    if not source.exists():
        pytest.skip("sibling plugin-stats checkout absent (CI checks out this repo alone)")
    assert _sha256(VENDORED) == _sha256(source)


def test_the_vendored_usage_client_is_lf_only():
    assert b"\r\n" not in VENDORED.read_bytes()
