#!/usr/bin/env python3
"""Refresh the public "streams matched" badge on the README.

WHAT THIS DOES. Adds up the stream-to-channel assignments Stream-Mapparr has
written, reading the plugin's own append-only tally inside the Dispatcharr
container, and writes a Shields.io endpoint document to a GitHub Gist. The
README badge points at that Gist, so this script is what makes the public number
change.

    python scripts/update_streams_matched_badge.py             # refresh the Gist
    python scripts/update_streams_matched_badge.py --dry-run   # print, write nothing
    python scripts/update_streams_matched_badge.py --create    # first-time Gist setup

WHAT COUNTS AS ONE. One stream-to-channel assignment written to the database. A
channel given four streams counts four.

THIS IS WORK DONE, NOT DISTINCT STREAMS. A daily schedule re-matches the same
library and counts the same streams again, so the number rises with use rather
than measuring how many different streams exist. That is the same thing the
sibling plugin's "streams checked" badge counts, and the README wording must not
imply a count of distinct streams.

WHAT IS DELIBERATELY NOT COUNTED. A dry run, which writes nothing to the
database. A run that assigned nothing, which is the correct and common outcome
with Overwrite Existing Streams off and an unchanged library. Sorting alternate
streams, which reorders assignments that already exist rather than making new
ones. Counting any of those would turn an assignment count into an activity
count.

HOW FAR BACK IT REACHES. To the deploy that introduced the tally, and no
further. The plugin's last-results file holds only the most recent run and a CSV
export exists only when the operator has CSV export switched on, so nothing
earlier can be recovered honestly. The file is append-only and never rotated, so
from that point it is a lifetime total. One line is about 90 bytes, and only
runs that actually assigned something write one.

PRIVACY. Only integers are read here, and only the total reaches the Gist. No
channel name, no stream name, no provider host and no path leave the machine.
The Gist is unlisted rather than private and the README names it, so treat the
number as public.
"""
import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

CONTAINER = "dispatcharr"
TALLY_PATH = "/data/stream_mapparr_match_counts.jsonl"
GIST_FILENAME = "stream-mapparr-streams-matched.json"
GIST_DESCRIPTION = "Stream-Mapparr streams matched badge (Shields.io endpoint)"

# The GitHub CLI is installed and authenticated here but is NOT on PATH in either
# shell, so `command -v gh` reports it missing and is not evidence. The path has
# to be pinned, but it is BUILT from LOCALAPPDATA rather than written out: this
# repository is public, and a literal path names the Windows account.
GH = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet",
                  "Packages",
                  "GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe",
                  "bin", "gh.exe")

# Where the Gist id is remembered between runs. Committed, so a re-clone updates
# the same document rather than silently creating a second one. The id is not a
# secret: the README badge URL names it.
STATE_PATH = ROOT / "scripts" / ".streams_matched_badge_gist"

LABEL = "streams matched"
COLOR = "blue"


def read_tally():
    """Return (total_streams, total_runs, malformed_lines) from the container.

    A malformed line is counted and skipped rather than being allowed to stop
    the run. The file is appended to by a live plugin, so a truncated final line
    is possible if a write was interrupted, and losing one run is better than
    publishing nothing.
    """
    result = subprocess.run(
        ["docker", "exec", CONTAINER, "cat", TALLY_PATH],
        capture_output=True, text=True)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "No such file" in stderr:
            raise SystemExit(
                "the tally file does not exist yet. It is created by the first "
                "Match and Assign run that actually assigns a stream, on a "
                "build that includes the tally. Nothing to publish yet.")
        raise SystemExit(f"could not read {TALLY_PATH}: {stderr}")

    total_streams = 0
    runs = 0
    malformed = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
            total_streams += int(record["streams"])
            runs += 1
        except (ValueError, KeyError, TypeError):
            malformed += 1
    return total_streams, runs, malformed


def endpoint_document(total):
    """The document Shields.io reads. Its schema, not ours."""
    return {"schemaVersion": 1, "label": LABEL, "message": f"{total:,}",
            "color": COLOR}


def gh(*args, check=True):
    result = subprocess.run([GH, *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def create_gist(path):
    url = gh("gist", "create", str(path), "--desc", GIST_DESCRIPTION)
    gist_id = url.rstrip("/").rsplit("/", 1)[-1]
    STATE_PATH.write_text(gist_id + "\n", encoding="utf-8")
    return gist_id, url


def raw_url(gist_id):
    return (f"https://gist.githubusercontent.com/PiratesIRC/{gist_id}"
            f"/raw/{GIST_FILENAME}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="print the number and the document, write nothing")
    parser.add_argument("--create", action="store_true",
                        help="create the Gist for the first time and remember its id")
    args = parser.parse_args()

    total, runs, malformed = read_tally()
    print(f"runs recorded:   {runs}")
    print(f"streams matched: {total:,}")
    if malformed:
        print(f"malformed lines skipped: {malformed}")

    document = endpoint_document(total)
    if args.dry_run:
        print(json.dumps(document, indent=2))
        return 0

    staged = ROOT / "dist" / GIST_FILENAME
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    if args.create:
        if STATE_PATH.exists():
            raise SystemExit(
                f"{STATE_PATH.name} already exists, so a Gist was created "
                f"before. Run without --create to update it.")
        gist_id, url = create_gist(staged)
        print(f"created gist {gist_id}")
        print(f"  page: {url}")
        print(f"  badge url: https://img.shields.io/endpoint?url={raw_url(gist_id)}")
        return 0

    if not STATE_PATH.exists():
        raise SystemExit(
            f"no {STATE_PATH.name}; run once with --create first.")
    gist_id = STATE_PATH.read_text(encoding="utf-8").strip()
    gh("gist", "edit", gist_id, "-a", str(staged))
    print(f"updated gist {gist_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
