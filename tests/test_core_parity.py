"""Vendored-core drift gate (Layer A) — runs in CI, needs no workspace _shared/.

The committed vendored shared files (matching_core.py, and later aliases_base.py) must
hash-match scripts/core_manifest.json. This catches a hand-edit to a vendored copy that
silently diverges from the _shared source of truth. To land an intended core change:
edit _shared/, re-run scripts/sync_core.py (re-vendors + rewrites the manifest), and
commit both. See MATCHER-STANDARDIZATION-PLAN.md §6.
"""
import hashlib
import json
import os

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INNER = os.path.join(_REPO, os.path.basename(_REPO))
_MANIFEST = os.path.join(_REPO, "scripts", "core_manifest.json")

with open(_MANIFEST, encoding="utf-8") as _fh:
    _PINS = json.load(_fh)


@pytest.mark.parametrize("fname", sorted(_PINS))
def test_vendored_core_matches_manifest(fname):
    path = os.path.join(_INNER, fname)
    assert os.path.exists(path), f"vendored {fname} is missing (run scripts/sync_core.py)"
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    assert digest == _PINS[fname], (
        f"{fname} vendored copy drifted from its pinned hash. If intended, edit _shared/, "
        f"re-run scripts/sync_core.py, and commit the updated core_manifest.json."
    )
