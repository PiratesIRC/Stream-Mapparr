#!/usr/bin/env python3
"""Fail if plugin.json version != PLUGIN_VERSION in plugin.py.

Shared by CI and the pre-commit hook. Exits non-zero on drift so a mismatched
build can never be committed or merged. Dispatcharr hot-reloads on plugin.json
mtime, so a drifted version means the UI advertises one build while another runs.
"""

import json
import re
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"


def main() -> int:
    plugin_json = PLUGIN_DIR / "plugin.json"
    plugin_py = PLUGIN_DIR / "plugin.py"

    json_version = json.loads(plugin_json.read_text(encoding="utf-8"))["version"]

    m = re.search(r'PLUGIN_VERSION\s*=\s*["\']([^"\']+)["\']',
                  plugin_py.read_text(encoding="utf-8"))
    if not m:
        print("ERROR: PLUGIN_VERSION not found in plugin.py", file=sys.stderr)
        return 2
    code_version = m.group(1)

    if json_version != code_version:
        print(f"VERSION DRIFT: plugin.json={json_version!r} != "
              f"plugin.py={code_version!r}\n"
              f"  -> run: python Stream-Mapparr/bump_version.py", file=sys.stderr)
        return 1

    print(f"version OK: {json_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
