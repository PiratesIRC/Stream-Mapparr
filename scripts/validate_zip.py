#!/usr/bin/env python3
r"""Validate a Dispatcharr-plugin release zip before upload.

Guards against the bug-087 class of packaging failures: a zip built with
PowerShell `Compress-Archive` / .NET Framework `ZipFile.CreateFromDirectory`
stores entry paths with backslash (`\`) separators. The ZIP spec mandates
forward slashes; on a Linux host these names are treated as flat literal
filenames, so Dispatcharr's install fails with
"missing plugin.py or package __init__.py".

IMPORTANT: Python's own `zipfile.namelist()` normalizes `\` -> `/` on read, so
it CANNOT detect this — check (a) parses the raw central-directory bytes.

Project-agnostic: the package directory is auto-detected from the zip (the
single top-level folder, or a root-level layout), so this script is drop-in for
any plugin without editing constants.

Checks:
  (a) every stored entry name uses forward-slash separators (no 0x5C bytes)
  (b) the installer's required files are present at the package root
      (plugin.json AND at least one of plugin.py / __init__.py)
  (c) no dev junk leaked in (.serena / .claude / __pycache__ / .git)

Exit 0 if all pass; non-zero with a report otherwise.

Usage: python scripts/validate_zip.py [path-to-zip]   (default: first *.zip found
       in CWD, else errors)
"""
import glob
import struct
import sys
import zipfile

JUNK = (".serena", ".claude", "__pycache__", ".git/", "/.git")
_CD_SIG = b"PK\x01\x02"  # central directory file header signature


def raw_entry_names(path):
    """Yield raw (undecoded-separator) entry name bytes from the central directory.

    We parse the central directory ourselves instead of using zipfile, because
    zipfile silently rewrites backslashes to forward slashes on read.
    """
    data = open(path, "rb").read()
    pos = data.find(_CD_SIG)
    while pos != -1:
        name_len = struct.unpack_from("<H", data, pos + 28)[0]
        extra_len = struct.unpack_from("<H", data, pos + 30)[0]
        comment_len = struct.unpack_from("<H", data, pos + 32)[0]
        yield data[pos + 46 : pos + 46 + name_len]
        pos = data.find(_CD_SIG, pos + 46 + name_len + extra_len + comment_len)


def detect_package_root(names):
    """Return (prefix, note). prefix is '' for a root layout or 'Pkg/' for a
    single-top-level-folder layout. note is None on success, else a diagnostic."""
    files = [n for n in names if not n.endswith("/")]
    root_files = [n for n in files if "/" not in n]
    top_dirs = sorted({n.split("/", 1)[0] for n in files if "/" in n})

    if any(f in ("plugin.py", "plugin.json", "__init__.py") for f in root_files):
        return "", None  # root layout (files at zip root)
    if len(top_dirs) == 1 and not root_files:
        return top_dirs[0] + "/", None
    if len(top_dirs) == 1:
        # one package dir plus stray root files — tolerate, package dir wins
        return top_dirs[0] + "/", None
    return None, f"cannot identify a single package root (top-level dirs={top_dirs}, root_files={root_files[:5]})"


def main(path):
    if not path:
        zips = sorted(glob.glob("*.zip"))
        if len(zips) != 1:
            print(f"FAIL: specify a zip path (found {len(zips)} *.zip in CWD)")
            return 1
        path = zips[0]

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

    # (b) required installer files at the auto-detected package root
    prefix, note = detect_package_root(names)
    if note:
        errors.append(note)
    else:
        nameset = set(names)
        if prefix + "plugin.json" not in nameset:
            errors.append(f"missing {prefix}plugin.json")
        if not ({prefix + "plugin.py", prefix + "__init__.py"} & nameset):
            errors.append(f"missing both {prefix}plugin.py and {prefix}__init__.py")

    # (c) dev junk
    junk = [n for n in names if any(j in n for j in JUNK)]
    if junk:
        errors.append(f"dev junk leaked into zip: {junk[:5]}")

    if errors:
        print(f"INVALID ZIP: {path}")
        for e in errors:
            print(f"  - {e}")
        return 1

    root = prefix or "(root)"
    print(f"OK: {path} ({len(names)} entries, forward-slash paths, package root '{root}' intact)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
