#!/usr/bin/env python3
r"""Validate a Stream-Mapparr release zip before upload.

Guards against the bug-087 class of packaging failures: a zip built with
PowerShell `Compress-Archive` / .NET Framework `ZipFile.CreateFromDirectory`
stores entry paths with backslash (`\`) separators. The ZIP spec mandates
forward slashes; on a Linux host these names are treated as flat literal
filenames, so Dispatcharr's install fails with
"missing plugin.py or package __init__.py".

IMPORTANT: Python's own `zipfile.namelist()` normalizes `\` -> `/` on read,
so it CANNOT detect this — check (a) parses the raw central-directory bytes.

Checks:
  (a) every stored entry name uses forward-slash separators (no 0x5C bytes)
  (b) the package-root files the installer needs are present
  (c) no dev junk leaked in (.serena / .claude / __pycache__ / .git)

Exit 0 if all pass; non-zero with a report otherwise.

Usage: python scripts/validate_zip.py [path-to-zip]   (default: Stream-Mapparr.zip)
"""
import struct
import sys
import zipfile

PKG = "Stream-Mapparr"
REQUIRED = [f"{PKG}/plugin.py", f"{PKG}/plugin.json", f"{PKG}/__init__.py"]
JUNK = (".serena", ".claude", "__pycache__", ".git")

_CD_SIG = b"PK\x01\x02"  # central directory file header signature


def raw_entry_names(path):
    """Yield raw (undecoded-separator) entry name bytes from the central directory.

    We parse the central directory ourselves instead of using zipfile, because
    zipfile silently rewrites backslashes to forward slashes on read.
    """
    data = open(path, "rb").read()
    pos = data.find(_CD_SIG)
    while pos != -1:
        # central dir header: name length is u16 at offset 28; name at offset 46
        name_len = struct.unpack_from("<H", data, pos + 28)[0]
        extra_len = struct.unpack_from("<H", data, pos + 30)[0]
        comment_len = struct.unpack_from("<H", data, pos + 32)[0]
        name = data[pos + 46 : pos + 46 + name_len]
        yield name
        pos = data.find(_CD_SIG, pos + 46 + name_len + extra_len + comment_len)


def main(path):
    try:
        names = zipfile.ZipFile(path).namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        print(f"FAIL: cannot read {path}: {exc}")
        return 1

    errors = []

    # (a) raw-byte separator check — zipfile.namelist() would hide this
    backslash = [n.decode("utf-8", "replace") for n in raw_entry_names(path) if b"\\" in n]
    if backslash:
        errors.append(
            "backslash path separators in stored names (rebuild with 7-Zip/zip.cmd "
            f"or git archive, NOT Compress-Archive): {backslash[:5]}"
        )

    # (b) required package-root files (namelist normalizes separators, fine here)
    missing = [r for r in REQUIRED if r not in names]
    if missing:
        errors.append(f"missing required package files: {missing}")

    # (c) dev junk
    junk = [n for n in names if any(j in n for j in JUNK)]
    if junk:
        errors.append(f"dev junk leaked into zip: {junk[:5]}")

    if errors:
        print(f"INVALID ZIP: {path}")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"OK: {path} ({len(names)} entries, forward-slash paths, package root intact)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "Stream-Mapparr.zip"))
