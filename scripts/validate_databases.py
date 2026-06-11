#!/usr/bin/env python3
"""Validate every *_channels.json against the structure the loader relies on.

Standalone (no pytest) so it can run in CI and the pre-commit hook even on a
machine without the dev deps. Mirrors the rules in tests/test_databases.py.
Exits non-zero (and prints every problem) if any database is malformed.
"""

import json
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"
BROADCAST_MARKER = "broadcast"


def validate_file(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [f"{path.name}: cannot parse JSON: {e}"]

    if not isinstance(data, dict) or not isinstance(data.get("channels"), list):
        return [f"{path.name}: top level must be an object with a 'channels' array"]
    if not data["channels"]:
        errors.append(f"{path.name}: 'channels' is empty")
    if not str(data.get("country_code", "")).strip():
        errors.append(f"{path.name}: missing 'country_code'")

    for i, c in enumerate(data["channels"]):
        ctype = str(c.get("type", "")).lower()
        if not ctype.strip():
            errors.append(f"{path.name}[{i}]: missing 'type'")
        if BROADCAST_MARKER in ctype:
            if not str(c.get("callsign", "")).strip():
                errors.append(f"{path.name}[{i}]: broadcast channel missing 'callsign'")
        else:
            if not str(c.get("channel_name", "")).strip():
                errors.append(f"{path.name}[{i}]: premium channel missing 'channel_name'")
        if "zones" in c:
            z = c["zones"]
            if not isinstance(z, list) or not z or any(not str(v).strip() for v in z):
                errors.append(f"{path.name}[{i}]: 'zones' must be a non-empty list of non-empty strings")
    return errors


def main() -> int:
    files = sorted(PLUGIN_DIR.glob("*_channels.json"))
    if not files:
        print("ERROR: no *_channels.json files found", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for path in files:
        errs = validate_file(path)
        all_errors.extend(errs)
        print(f"{'FAIL' if errs else 'ok  '}  {path.name}"
              + (f"  ({len(errs)} problem(s))" if errs else ""))

    if all_errors:
        print(f"\n{len(all_errors)} problem(s):", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print(f"\nall {len(files)} database(s) valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
