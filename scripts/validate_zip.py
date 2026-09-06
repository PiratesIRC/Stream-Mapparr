#!/usr/bin/env python3
r"""Validate a Dispatcharr plugin release zip before it is uploaded anywhere.

Checks, each of which has caught a real defect in this workspace:
  (a) no backslash in any stored entry name, read from the RAW central
      directory because zipfile.namelist() normalises them away (bug-087:
      PowerShell Compress-Archive breaks install on Linux)
  (b) every entry sits under <package_dir>/ and plugin.json plus plugin.py
      exist there (the loader contract)
  (c) no development file leaked in: __pycache__, .pyc, .claude, CLAUDE.md,
      .wolf, settings.local.json, bump_version.py, tests/
  (d) no TEXT entry contains CRLF (bug-118: the worktree here is CRLF and a
      bare git archive writes it into every file); binary entries such as
      .png are skipped, because those bytes occur naturally in them

Usage: python scripts/validate_zip.py <zip> [--repo PATH]
Exit 0 on success, 1 on any failure. Importable: validate(path, package_dir).
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from release_config import find_repo_root, load_config  # noqa: E402

TEXT_SUFFIXES = (".py", ".json", ".md", ".txt", ".csv", ".html", ".js", ".css",
                 ".yml", ".yaml", ".ini", ".cfg", ".toml", ".sh", ".ps1")
FORBIDDEN = ("__pycache__", ".claude", "CLAUDE.md", ".wolf", "settings.local.json",
             "bump_version.py", "tests/", ".pytest_cache", ".git/")
_CD_SIG = b"PK\x01\x02"


_EOCD_SIG = b"PK\x05\x06"


def raw_entry_names(path: Path):
    """Yield raw entry-name bytes from the central directory.

    The directory is located through the end-of-central-directory record
    (searched in the last 64 KiB, where the spec puts it) rather than by
    scanning from byte 0, because compressed payload can contain the header
    signature by chance and would yield a bogus name length. A truncated
    directory raises struct.error, which the caller reports as unreadable.
    """
    data = path.read_bytes()
    tail = data[-65_557:]
    eocd = tail.rfind(_EOCD_SIG)
    if eocd == -1:
        raise struct.error("no end-of-central-directory record")
    eocd += len(data) - len(tail)
    entries = struct.unpack_from("<H", data, eocd + 10)[0]
    pos = struct.unpack_from("<I", data, eocd + 16)[0]
    for _ in range(entries):
        if data[pos:pos + 4] != _CD_SIG:
            raise struct.error("central directory header signature missing")
        name_len = struct.unpack_from("<H", data, pos + 28)[0]
        extra_len = struct.unpack_from("<H", data, pos + 30)[0]
        comment_len = struct.unpack_from("<H", data, pos + 32)[0]
        yield data[pos + 46: pos + 46 + name_len]
        pos += 46 + name_len + extra_len + comment_len


def _leaked(name: str) -> bool:
    """A forbidden marker must be a whole path segment (or a suffix), so
    `contests/` or `.wolfram.py` are not caught by the `tests/` and `.wolf`
    markers."""
    parts = name.split("/")
    for marker in FORBIDDEN:
        segment = marker.rstrip("/")
        if segment in parts:
            return True
    return name.endswith(".pyc")


def validate(path: Path, package_dir: str) -> tuple[list[str], dict]:
    errors: list[str] = []
    info: dict = {}
    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"cannot read {path}: {exc}"], info

    try:
        backslash = [n.decode("utf-8", "replace") for n in raw_entry_names(path) if b"\\" in n]
    except struct.error as exc:
        return [f"cannot read the central directory of {path}: {exc}"], info
    if backslash:
        errors.append(f"backslash path separators in stored names: {backslash[:5]}")

    prefix = package_dir.rstrip("/") + "/"
    names = [n for n in zf.namelist() if not n.endswith("/")]
    outside = [n for n in names if not n.startswith(prefix)]
    if outside:
        errors.append(f"entries outside {prefix}: {outside[:5]}")
    for required in ("plugin.json", "plugin.py"):
        if prefix + required not in names:
            errors.append(f"missing {prefix}{required}")

    leaked = [n for n in names if _leaked(n)]
    if leaked:
        errors.append(f"development files leaked into the zip: {leaked[:8]}")

    crlf = []
    text_count = 0
    for name in names:
        if name.lower().endswith(TEXT_SUFFIXES):
            text_count += 1
            if b"\r\n" in zf.read(name):
                crlf.append(name)
    if crlf:
        errors.append(f"CRLF line endings in {len(crlf)} text file(s): {crlf[:8]}")

    version = None
    if prefix + "plugin.json" in names:
        try:
            version = json.loads(zf.read(prefix + "plugin.json").decode("utf-8")).get("version")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"plugin.json inside the zip is unreadable: {exc}")
    info.update(entries=len(names), text_files=text_count, version=version)
    return errors, info


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zip")
    parser.add_argument("--repo")
    parser.add_argument("--package-dir", help="override the package dir (skips release.json)")
    args = parser.parse_args()
    if args.package_dir:
        package_dir = args.package_dir
    else:
        repo = Path(args.repo).resolve() if args.repo else find_repo_root()
        package_dir = load_config(repo)["package_dir"]
    errors, info = validate(Path(args.zip), package_dir)
    if errors:
        print(f"INVALID ZIP: {args.zip}")
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print(f"OK: {args.zip} ({info['entries']} entries, {info['text_files']} text files, "
          f"version {info['version']}, package root {package_dir}/)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
