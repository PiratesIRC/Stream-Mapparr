#!/usr/bin/env python3
"""Shared helpers for the release tooling vendored into every plugin repository.

Every tool reads `release.json` at the repository root. The keys that matter
here:

  package_dir     inner deployable folder name
  tag_prefix      "v" or ""
  changelog       changelog path relative to the repository root
  zip_name        release asset filename, with {version} substituted
  version_files   list of {"path": ..., "regex": ...}; each regex has exactly
                  one capture group around the version string and must match
                  exactly once in that file. plugin.json is always first.

Vendored by `_shared/tools/sync_tools.py`; do not hand-edit the copy under a
repository's scripts/ directory.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NoReturn

CALVER = re.compile(r"^\d+\.\d{2}\.\d{7}$")
CONFIG_NAME = "release.json"


def fail(message: str, code: int = 1) -> NoReturn:
    print(f"FAIL: {message}")
    sys.exit(code)


def read_raw(path: Path) -> str:
    """Read without newline translation so CRLF files stay CRLF on write."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_raw(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def find_repo_root(start: Path | None = None) -> Path:
    """Walk upward from start (default: cwd) until release.json is found."""
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    fail(f"no {CONFIG_NAME} found in {here} or any parent")


def load_config(repo: Path) -> dict:
    path = repo / CONFIG_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{path} does not exist")
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")
    for key in ("package_dir", "tag_prefix", "changelog", "zip_name", "version_files"):
        if key not in data:
            fail(f"{path} is missing required key {key!r}")
    if not data["version_files"] or not data["version_files"][0]["path"].endswith("plugin.json"):
        fail(f"{path}: version_files must start with the plugin.json entry")
    return data


def read_versions(repo: Path, config: dict) -> list[tuple[str, str]]:
    """Return [(relative path, version)] for every version_files entry."""
    found = []
    for entry in config["version_files"]:
        path = repo / entry["path"]
        if not path.is_file():
            # An entry marked optional is a gitignored local file (iptv_checker
            # stamps its version into its notes file). It is checked when
            # present and skipped with a notice on a clean checkout, where CI
            # would otherwise fail on a file that never ships.
            if entry.get("optional"):
                print(f"{entry['path']}: absent (optional, skipped)")
                continue
            fail(f"version file {entry['path']} does not exist")
        text = read_raw(path)
        matches = re.findall(entry["regex"], text, flags=re.MULTILINE)
        if len(matches) != 1:
            fail(f"{entry['path']}: version regex matched {len(matches)} times, expected exactly 1")
        value = matches[0] if isinstance(matches[0], str) else matches[0][0]
        found.append((entry["path"], value))
    return found


def write_version(repo: Path, entry: dict, new_version: str) -> None:
    path = repo / entry["path"]
    if not path.is_file() and entry.get("optional"):
        print(f"{entry['path']}: absent (optional, not written)")
        return
    text = read_raw(path)
    pattern = re.compile(entry["regex"], flags=re.MULTILINE)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        fail(f"{entry['path']}: version regex matched {len(matches)} times, expected exactly 1")
    match = matches[0]
    start, end = match.span(1)
    text = text[:start] + new_version + text[end:]
    # read_raw and write_raw do no newline translation, so a CRLF worktree
    # file stays CRLF and an LF file stays LF; only the version bytes change.
    write_raw(path, text)


def changelog_top_version(repo: Path, config: dict) -> str | None:
    path = repo / config["changelog"]
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("## "):
            match = re.search(r"v?(\d+\.\d+\.\d+)", line)
            return match.group(1) if match else None
    # A changelog kept as an index table (Event-Channel-Managarr) has no
    # headings; its first calver-shaped token is the newest row.
    match = re.search(r"\bv?(\d+\.\d{2}\.\d{7})\b", text)
    return match.group(1) if match else None


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _private_remote(rules_path: str, url: str) -> int:
    """Exit 0 when the remote URL matches a private_remotes entry in the
    publish-audit rules file, else 1. Used by the generated pre-push hook."""
    try:
        rules = json.loads(Path(rules_path).read_text(encoding="utf-8"))
    except Exception:
        return 1
    for entry in rules.get("private_remotes", []):
        pattern = entry.get("pattern")
        if not pattern:
            # An empty pattern would match every URL and silence the audit
            # for a public remote; treat it as a rules-file error.
            print("[pre-push] private_remotes entry with an empty pattern is refused")
            return 1
        if re.search(pattern, url):
            print("[pre-push] PRIVATE-REMOTE-MATCH " + entry.get("why", "private remote by rule"))
            return 0
    return 1


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--private-remote":
        sys.exit(_private_remote(sys.argv[2], sys.argv[3]))
    print("release_config.py is a helper module; the only CLI is --private-remote RULES URL")
    sys.exit(2)
