#!/usr/bin/env python3
"""Build the release zip from the git INDEX (never the working tree) and
validate it.

    git -c core.autocrlf=false -c core.eol=lf archive --format=zip \
        -o <out>/<zip_name> <ref> -- <package_dir>

run with TZ=UTC. The two -c flags are load-bearing on this machine: without
them git archive writes CRLF into every text file (measured 27 of 27 files).
Archiving the commit (not the tree) stamps entries with the commit time, and
TZ=UTC keeps those DOS timestamps the same on every machine, so one tag gives
one sha256. Building from the index also excludes gitignored working files
that a directory copy would ship.

Usage: python scripts/build_release_zip.py [--ref REF] [--out DIR] [--repo PATH]
Prints the zip path and its sha256. Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_config import fail, find_repo_root, load_config  # noqa: E402
from validate_zip import validate  # noqa: E402

# git archive writes each entry's DOS timestamp in LOCAL time, so the same
# commit gives different bytes on machines in different timezones. Pinning TZ
# to UTC makes the zip depend only on the commit and the tree.
UTC_ENV = dict(os.environ, TZ="UTC")


def git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            env=UTC_ENV)
    if result.returncode != 0:
        fail(f"git {' '.join(args)} exited {result.returncode}: "
             f"{result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--out", default="dist")
    args = parser.parse_args()

    repo = Path(args.repo).resolve() if args.repo else find_repo_root()
    config = load_config(repo)
    package_dir = config["package_dir"]

    manifest = git(repo, "show", f"{args.ref}:{package_dir}/plugin.json")
    version = json.loads(manifest.decode("utf-8"))["version"]
    zip_name = config["zip_name"].replace("{version}", version)
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = repo / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / zip_name

    # Archive the COMMIT with the package dir as a pathspec, not the tree
    # ref:package_dir. A tree archive stamps every entry with the build time,
    # so two builds of one tag never matched; a commit archive stamps each
    # entry with the commit's committer time. The pathspec keeps the entry
    # names identical to the old --prefix form (package_dir/...).
    git(repo, "-c", "core.autocrlf=false", "-c", "core.eol=lf", "archive",
        "--format=zip", "-o", str(zip_path), args.ref, "--", package_dir)

    errors, info = validate(zip_path, package_dir)
    if errors:
        print(f"INVALID ZIP: {zip_path}")
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    if info.get("version") != version:
        fail(f"zip plugin.json version {info.get('version')} != {version} at {args.ref}")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"built {zip_path} from {args.ref}:{package_dir}")
    print(f"entries={info['entries']} text_files={info['text_files']} version={version}")
    print(f"sha256={digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
