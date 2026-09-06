#!/usr/bin/env python3
"""Bump the plugin's calver version in every file release.json names, and write
a changelog stub.

Calver: Major.YY.DDDHHMM in UTC. Major is kept from the current version unless
--major is given. UTC, not local time: two repositories once stamped local time
and could produce a version that sorted before its predecessor across a
timezone change.

Usage:
    python scripts/bump_version.py                 compute from now, write
    python scripts/bump_version.py --dry-run       show old and new, write nothing
    python scripts/bump_version.py --set 1.26.2501200
    python scripts/bump_version.py --major 2
    python scripts/bump_version.py --no-changelog  skip the changelog stub

Never commits or tags. Exit 1 on any refusal.
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_config import (  # noqa: E402
    CALVER,
    changelog_top_version,
    fail,
    find_repo_root,
    load_config,
    read_raw,
    read_versions,
    version_key,
    write_raw,
    write_version,
)


def compute_version(major: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"{major}.{now:%y}.{now.timetuple().tm_yday:03d}{now:%H%M}"


def changelog_stub(version: str, today: str) -> str:
    return f"## {version} ({today})\n\n### Changed\n\n- \n\n"


def insert_stub(path: Path, version: str) -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    stub = changelog_stub(version, today)
    if path.is_file():
        text = read_raw(path)
        newline = "\r\n" if "\r\n" in text else "\n"
        stub = stub.replace("\n", newline)
        lines = text.splitlines(keepends=True)
        # Insert before the first "## " heading so a title line stays on top;
        # for an index-table changelog insert before the table header.
        index = next((i for i, line in enumerate(lines)
                      if line.startswith("## ") or line.startswith("| Version")), len(lines))
        if index == len(lines) and text and not text.endswith(newline):
            lines.append(newline)
        lines[index:index] = [stub]
        write_raw(path, "".join(lines))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# Changelog\n\n{stub}", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", help="repository root (default: found from cwd)")
    parser.add_argument("--set", dest="explicit", help="explicit version string")
    parser.add_argument("--major", help="override the major component")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-changelog", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo).resolve() if args.repo else find_repo_root()
    config = load_config(repo)
    versions = read_versions(repo, config)
    current = versions[0][1]
    drift = [(p, v) for p, v in versions if v != current]
    if drift:
        print("WARNING: files disagreed before the bump:")
        for p, v in drift:
            print(f"  {p}: {v}")

    if args.explicit:
        new = args.explicit
    else:
        major = args.major or current.split(".", 1)[0]
        new = compute_version(major)

    if not CALVER.match(new):
        fail(f"{new!r} is not calver Major.YY.DDDHHMM")
    if not CALVER.match(current):
        print(f"WARNING: current version {current!r} is not calver; comparing anyway")
    try:
        if version_key(new) <= version_key(current):
            fail(f"new version {new} does not sort after current {current}")
    except ValueError:
        pass

    print(f"current: {current}")
    print(f"new:     {new}")
    for entry in config["version_files"]:
        print(f"  will update {entry['path']}")
    if not args.no_changelog:
        print(f"  will add stub to {config['changelog']}")
    if args.dry_run:
        print("(dry-run: nothing written)")
        return 0

    for entry in config["version_files"]:
        write_version(repo, entry, new)
    after = read_versions(repo, config)
    bad = [(p, v) for p, v in after if v != new]
    if bad:
        fail(f"post-bump mismatch: {bad}")
    if not args.no_changelog:
        insert_stub(repo / config["changelog"], new)
        if changelog_top_version(repo, config) != new:
            fail("changelog stub was written but the top heading does not read the new version")
    print(f"bumped {current} -> {new} in {len(after)} file(s)")
    print("Next: fill in the changelog entry, then run scripts/check_version_sync.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
