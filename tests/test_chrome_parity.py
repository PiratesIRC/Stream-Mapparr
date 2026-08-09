"""Drift gate for the vendored report page furniture.

report_chrome.py holds the stylesheet, masthead, stat tiles, bar chart,
collapsible sections and footer shared with the other reporting plugin here.
It is vendored rather than imported: a plugin deploys as a self-contained
directory into /data/plugins, where the workspace _shared path does not exist.

The point of sharing it is that the two reports cannot drift apart. A hand-edit
to this copy would defeat that silently, so the committed file must hash-match
scripts/chrome_manifest.json. To land an intended change: edit
_shared/report_chrome.py, re-vendor, update the manifest, and commit together.

Runs in CI, and deliberately needs no access to the workspace _shared directory,
which is not part of this repository.
"""
import hashlib
import json
import os

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INNER = next(
    (os.path.join(_REPO, _e) for _e in sorted(os.listdir(_REPO))
     if os.path.isfile(os.path.join(_REPO, _e, "fuzzy_matcher.py"))),
    os.path.join(_REPO, os.path.basename(_REPO)),
)
_MANIFEST = os.path.join(_REPO, "scripts", "chrome_manifest.json")

with open(_MANIFEST, encoding="utf-8") as _fh:
    _PINS = json.load(_fh)


@pytest.mark.parametrize("fname", sorted(_PINS))
def test_vendored_chrome_matches_manifest(fname):
    path = os.path.join(_INNER, fname)
    assert os.path.exists(path), f"vendored {fname} is missing"
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert digest == _PINS[fname], (
        f"{fname} drifted from its pinned hash. If the change is intended, edit "
        f"_shared/report_chrome.py, re-vendor it, update scripts/chrome_manifest.json, "
        f"and commit them together."
    )


@pytest.mark.parametrize("fname", sorted(_PINS))
def test_vendored_chrome_uses_unix_line_endings(fname):
    """A formatter rewriting the shared source with Windows line endings has
    broken a hash pin here before: the file looked unchanged and the hash did
    not match on Linux."""
    path = os.path.join(_INNER, fname)
    assert b"\r\n" not in open(path, "rb").read()
