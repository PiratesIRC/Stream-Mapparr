#!/usr/bin/env python3
"""Bump the Stream-Mapparr plugin version in plugin.json and plugin.py.

Version format: 1.26.{DDD}{HHMM} where DDD is day-of-year (3 digits) and
HHMM is 4-digit UTC time. Matches the Lineuparr / Channel-Mapparr /
EPG-Janitor / IPTV-Checker cohort convention. Pass a version string to
override.

Usage:
    python3 bump_version.py              # auto, current timestamp
    python3 bump_version.py 1.26.1081958 # explicit

Exit codes: 0 on success, non-zero if the two files disagreed before/after.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PLUGIN_JSON = ROOT / "plugin.json"
PLUGIN_PY = ROOT / "plugin.py"

VERSION_RE = re.compile(r'^\d+\.\d+\.\d{7}$')
# Stream-Mapparr keeps the version on a class attribute inside PluginConfig.
PY_VERSION_RE = re.compile(r'(^\s*PLUGIN_VERSION\s*=\s*)"([^"]+)"', re.MULTILINE)


def auto_version() -> str:
    now = datetime.now(timezone.utc)
    return f"1.26.{now.timetuple().tm_yday:03d}{now.strftime('%H%M')}"


def read_json_version() -> str:
    return json.loads(PLUGIN_JSON.read_text())["version"]


def read_py_version() -> str:
    m = PY_VERSION_RE.search(PLUGIN_PY.read_text())
    if not m:
        raise RuntimeError("PLUGIN_VERSION attribute not found in plugin.py")
    return m.group(2)


def write_json_version(new: str) -> None:
    text = PLUGIN_JSON.read_text()
    updated = re.sub(r'("version"\s*:\s*)"[^"]+"', f'\\1"{new}"', text, count=1)
    PLUGIN_JSON.write_text(updated)


def write_py_version(new: str) -> None:
    text = PLUGIN_PY.read_text()
    updated = PY_VERSION_RE.sub(lambda m: f'{m.group(1)}"{new}"', text, count=1)
    PLUGIN_PY.write_text(updated)


def main(argv: list[str]) -> int:
    new = argv[1] if len(argv) > 1 else auto_version()
    if not VERSION_RE.match(new):
        print(f"error: version '{new}' must match 1.X.DDDHHMM (e.g. 1.26.1081958)", file=sys.stderr)
        return 2

    before_json = read_json_version()
    before_py = read_py_version()
    if before_json != before_py:
        print(f"warning: plugin.json ({before_json}) and plugin.py ({before_py}) disagreed before bump", file=sys.stderr)

    write_json_version(new)
    write_py_version(new)

    after_json = read_json_version()
    after_py = read_py_version()
    if after_json != after_py or after_json != new:
        print(f"error: post-bump mismatch json={after_json} py={after_py} target={new}", file=sys.stderr)
        return 1

    print(f"bumped {before_json} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
