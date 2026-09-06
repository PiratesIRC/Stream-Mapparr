#!/usr/bin/env python3
"""Fail unless every version release.json names agrees, the changelog's top
entry matches, and (with --tag) the tag matches too.

Usage:
    python scripts/check_version_sync.py
    python scripts/check_version_sync.py --tag v1.26.2501200

Dispatcharr hot-reloads on plugin.json's mtime and reads the version from the
imported module, so a drift means the card advertises one build while another
runs. Exit 1 on any mismatch, 0 when everything agrees.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_config import (  # noqa: E402
    CALVER,
    changelog_top_version,
    find_repo_root,
    load_config,
    read_versions,
)


def description_in_sync(repo: Path, config: dict) -> str | None:
    """Optional: when release.json sets description_sync, the Plugin class
    description in plugin.py must equal plugin.json's. Returns an error or None."""
    if not config.get("description_sync"):
        return None
    package = repo / config["package_dir"]
    manifest = json.loads((package / "plugin.json").read_text(encoding="utf-8"))
    tree = ast.parse((package / "plugin.py").read_text(encoding="utf-8"))
    class_description = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Plugin":
            for statement in node.body:
                targets = []
                if isinstance(statement, ast.Assign):
                    targets = statement.targets
                elif isinstance(statement, ast.AnnAssign):
                    targets = [statement.target]
                if (targets
                        and any(getattr(t, "id", None) == "description" for t in targets)
                        and isinstance(getattr(statement, "value", None), ast.Constant)
                        and isinstance(statement.value.value, str)):
                    class_description = statement.value.value
    if class_description is None:
        return "Plugin.description not found in plugin.py"
    if class_description != manifest.get("description"):
        return ("description mismatch:\n"
                f"  plugin.py  : {class_description}\n"
                f"  plugin.json: {manifest.get('description')}")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo")
    parser.add_argument("--tag", help="tag name that must equal tag_prefix + version")
    args = parser.parse_args()

    repo = Path(args.repo).resolve() if args.repo else find_repo_root()
    config = load_config(repo)
    versions = read_versions(repo, config)
    reference = versions[0][1]
    errors = []
    for path, value in versions:
        print(f"{path}: {value}")
        if value != reference:
            errors.append(f"{path} has {value}, plugin.json has {reference}")
    if not CALVER.match(reference):
        errors.append(f"plugin.json version {reference!r} is not calver Major.YY.DDDHHMM")

    top = changelog_top_version(repo, config)
    if (repo / config["changelog"]).is_file():
        print(f"{config['changelog']} top entry: {top}")
        if top != reference:
            errors.append(f"changelog top entry is {top}, plugin.json has {reference}")
    else:
        print(f"{config['changelog']}: absent (no changelog check)")

    if args.tag is not None:
        prefix = config["tag_prefix"]
        if not args.tag.startswith(prefix) or args.tag[len(prefix):] != reference:
            errors.append(f"tag {args.tag!r} does not equal {prefix!r} + {reference!r}")
        else:
            print(f"tag {args.tag}: matches")

    description_error = description_in_sync(repo, config)
    if description_error:
        errors.append(description_error)

    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(f"version OK: {reference}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
