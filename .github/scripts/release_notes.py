"""Write the changelog entry for one version to a file, for ``gh release create``.

The release job passes the file as ``--notes-file``. The entry is the section
under the first ``## `` heading that contains the version string, read from
the first changelog that exists among the candidates: the path named in
release.json, ``<package_dir>/CHANGELOG.md``, ``CHANGELOG.md`` and
``docs/CHANGELOG.md``.

When no changelog exists or no heading names the version, a short stub is
written and a ``::warning::`` annotation is emitted, and the exit status is
still 0. The release is not blocked on prose: the version, archive and
checksum checks upstream of this step are the gates, and a stub note is
visible on the release page where a person can replace it. Pass ``--strict``
to make a missing entry fail instead.

This file is vendored from _shared/ci/release_notes.py by
_shared/ci/generate_ci.py; edit the shared copy and regenerate.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys


def candidates(changelog: str | None, package_dir: str | None) -> list[str]:
    seen: list[str] = []
    for item in (changelog,
                 f"{package_dir}/CHANGELOG.md" if package_dir else None,
                 "CHANGELOG.md", "docs/CHANGELOG.md"):
        if item and item not in seen:
            seen.append(item)
    return seen


def extract(text: str, version: str) -> str | None:
    """Return the body of the first ``## `` section whose heading names version."""
    lines = text.splitlines()
    heading_re = re.compile(r"^##\s+(.*)$")
    start = None
    for index, line in enumerate(lines):
        match = heading_re.match(line)
        if not match:
            continue
        if start is not None:
            return "\n".join(lines[start:index]).strip() + "\n"
        if version in match.group(1):
            start = index
    if start is not None:
        return "\n".join(lines[start:]).strip() + "\n"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--changelog", default=None)
    parser.add_argument("--package-dir", default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    notes = None
    used = None
    for candidate in candidates(args.changelog, args.package_dir):
        path = pathlib.Path(candidate)
        if not path.is_file():
            continue
        used = candidate
        notes = extract(path.read_text(encoding="utf-8"), args.version)
        if notes:
            break

    if not notes:
        reason = ("no changelog file found" if used is None
                  else f"no '## ' heading in {used} names {args.version}")
        if args.strict:
            sys.exit(f"release notes: {reason}")
        print(f"::warning title=Release notes stub::{reason}; a stub was "
              f"written. Edit the release notes on the release page.")
        where = used or "the repository changelog"
        notes = (f"Release {args.version}.\n\nSee {where} for the change "
                 f"record.\n")
    else:
        print(f"release notes: {len(notes.splitlines())} line(s) from {used}")

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(notes, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
