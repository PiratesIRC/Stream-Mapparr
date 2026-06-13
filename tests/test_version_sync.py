"""plugin.json version MUST equal PLUGIN_VERSION in plugin.py.

This drift is a documented failure mode: Dispatcharr hot-reloads on plugin.json
mtime, so a version that disagrees with the code means the UI advertises one
build while another runs. bump_version.py keeps them in sync; this test fails
loudly if a hand-edit breaks that.
"""

import json
import re
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"


def _json_version():
    with open(PLUGIN_DIR / "plugin.json", encoding="utf-8") as f:
        return json.load(f)["version"]


def _code_version():
    text = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    m = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']', text)
    assert m, "PLUGIN_VERSION assignment not found in plugin.py"
    return m.group(1)


def test_versions_match():
    assert _json_version() == _code_version(), (
        f"version drift: plugin.json={_json_version()!r} "
        f"plugin.py={_code_version()!r} — run bump_version.py"
    )


def test_version_is_calver():
    """Bare calver 1.MAJOR.DDDHHMM, no 'v' prefix (matches release tag style)."""
    assert re.fullmatch(r"1\.\d+\.\d+", _json_version()), \
        f"{_json_version()!r} is not bare calver 1.MAJOR.DDDHHMM"
