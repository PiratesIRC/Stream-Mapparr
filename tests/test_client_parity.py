"""The vendored Newsflasharr client must never drift from its pin.

Hand-editing the vendored copy is how a caller silently stops matching the
service contract. The pin makes drift a build failure rather than a runtime
surprise. The workflow when the shared client changes is to re-copy the whole
file and update the pin, never to patch the vendored copy in place.
"""
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDORED = ROOT / "Stream-Mapparr" / "notify_client.py"
MANIFEST = ROOT / "scripts" / "client_manifest.json"


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_vendored_client_exists():
    assert VENDORED.is_file(), (
        "Stream-Mapparr/notify_client.py is missing. Copy it from "
        "<workspace>/_shared/notify_client.py without modification."
    )


def test_the_vendored_client_matches_its_pin():
    pinned = json.loads(MANIFEST.read_text(encoding="utf-8"))["notify_client.py"]
    assert _sha256(VENDORED) == pinned, (
        "Stream-Mapparr/notify_client.py drifted from scripts/client_manifest.json. "
        "Re-copy the whole file from the shared source, do not patch it in place, "
        "then update the pin."
    )


def test_the_vendored_client_matches_the_shared_source_when_present():
    """Skipped where the sibling workspace directory is absent, for example on a
    continuous integration runner that checks out this repository alone."""
    shared = ROOT.parent / "_shared" / "notify_client.py"
    if not shared.exists():
        return
    assert _sha256(VENDORED) == _sha256(shared), (
        "The vendored copy no longer matches <workspace>/_shared/notify_client.py."
    )
