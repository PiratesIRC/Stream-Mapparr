"""Scan the tracked tree against the repository-specific publish-audit rules.

Second line of defence behind the workspace audit skill and the pre-push hook.
Those run on a maintainer machine; this runs on the server, where a
``git push --no-verify`` or an uninstalled hook does not apply. It applies ONLY
the deny and allow lists from the rules file. It does not carry the generic
credential, hostname and entropy patterns, and it cannot scan history.

Where the rules come from, in order:

1. The ``PUBLISH_AUDIT_RULES`` environment variable, holding the rules as
   JSON. Continuous integration fills it from a repository secret. When the
   variable is set but does not parse, that is an error, never a fallback.
2. ``.publish-audit.json`` in the repository root, when the repository
   declares its rules committed (``AUDIT_RULES_COMMITTED`` is ``true``, the
   default). A repository that declares them committed and lacks the file is
   an error.
3. When the rules are declared NOT committed and the file exists anyway, it
   must be untracked (a maintainer machine). A TRACKED copy in such a
   repository is a leak in progress and fails the scan outright.
4. Otherwise, on a ``pull_request`` event the scan is skipped with a notice,
   because GitHub withholds repository secrets from fork and Dependabot pull
   requests and failing there reports the absence of a secret, not a problem
   with the change. Merging such a change is a push, and a push without the
   rules FAILS.

What is printed. Never a pattern and never the matched text: on a real
finding the matched text IS the secret, and Actions logs follow repository
visibility. Findings name the file, the line and the deny rule's POSITION.
The rule's ``why`` is printed only when the rules came from a committed file,
which is already public wherever the log is.

Usage (identical locally and on a runner):

    python .github/scripts/publish_audit_ci.py
    python .github/scripts/publish_audit_ci.py --rules other.json

Exit status is 1 on any finding or any configuration error, so it works as a
gate. This file is vendored from _shared/ci/publish_audit_ci.py by
_shared/ci/generate_ci.py; edit the shared copy and regenerate.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

ENV_RULES = "PUBLISH_AUDIT_RULES"
ENV_COMMITTED = "AUDIT_RULES_COMMITTED"
ENV_EVENT = "GITHUB_EVENT_NAME"
DEFAULT_RULES_FILE = ".publish-audit.json"

# Binary formats that are not a text leak surface. Anything else that does not
# decode as UTF-8 is skipped too, and every skip is listed so a silent gap is
# visible in the log.
SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svgz",
    ".zip", ".gz", ".bz2", ".xz", ".7z",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".pyc", ".pdf",
}


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(("git",) + args, capture_output=True, text=True)


def _is_tracked(path: str) -> bool:
    return _git("ls-files", "--error-unmatch", "--", path).returncode == 0


def load_rules(rules_file: str, committed: bool, event: str):
    """Return (rules dict, source label, why_is_public) or exit.

    Exits 0 with a notice only in the one case where the rules cannot be
    present: a pull request run with no secret and no committed file.
    """
    raw = os.environ.get(ENV_RULES, "").strip()
    if raw:
        try:
            return json.loads(raw), f"the {ENV_RULES} secret", False
        except json.JSONDecodeError as exc:
            sys.exit(
                f"{ENV_RULES} is set but does not parse as JSON: {exc}. Fix "
                f"the secret rather than unsetting it; an unset secret would "
                f"stop the scan covering anything.")

    path = pathlib.Path(rules_file)
    if committed:
        if path.exists():
            return (json.loads(path.read_text(encoding="utf-8")),
                    rules_file, True)
        sys.exit(
            f"this repository declares its publish-audit rules committed "
            f"({ENV_COMMITTED} is true) but {rules_file} is absent from the "
            f"checkout. The deny list is the half that catches real leaks, "
            f"so a missing one is an error, not a reason to skip the scan.")

    # Rules declared NOT committed. A tracked copy is the very leak the rules
    # exist to prevent, so that is checked before anything else.
    if path.exists() and _is_tracked(rules_file):
        sys.exit(
            f"{rules_file} is TRACKED by git, but this repository declares "
            f"its rules uncommitted. The file spells out every string it "
            f"exists to keep out. Remove it from the index (keep the local "
            f"copy) before this reaches a remote.")
    if path.exists():
        return (json.loads(path.read_text(encoding="utf-8")),
                f"{rules_file} (untracked, maintainer machine)", False)

    if event == "pull_request":
        print(
            "::notice title=Publish audit skipped::No deny list is reachable "
            "from this pull request, because GitHub withholds repository "
            "secrets from fork and Dependabot pull requests. This says "
            "nothing about the change. The audit runs in full when the "
            "change is pushed to the default branch, which is what merging "
            "does.")
        sys.exit(0)

    sys.exit(
        f"no publish-audit rules available on a {event or 'local'} run: "
        f"{ENV_RULES} is unset or empty and {rules_file} does not exist. "
        f"Set the {ENV_RULES} repository secret. A check whose input is "
        f"absent must fail, never report a clean result it could not "
        f"compute.")


def compile_rules(rules: dict, source: str):
    """Validate the schema and compile every pattern, reporting by position."""
    problems = []
    deny, allow = [], []
    for group, reason_key, target in (("deny", "why", deny),
                                      ("allow", "reason", allow)):
        entries = rules.get(group)
        if entries is None:
            entries = []
        if group == "deny" and not entries:
            problems.append("the deny list is missing or empty: refusing to "
                            "report a vacuous pass")
        for number, rule in enumerate(entries, start=1):
            pattern = rule.get("pattern") if isinstance(rule, dict) else None
            if not pattern:
                problems.append(f"{group} rule {number} has no pattern")
                continue
            try:
                # Deny rules are case-insensitive: a host or an account
                # fragment is the same leak in any case. Allow rules match
                # the exact text they were written against.
                flags = re.IGNORECASE if group == "deny" else 0
                compiled = re.compile(pattern, flags)
            except re.error as exc:
                # str(exc) carries the message and position only; the
                # pattern is on exc.pattern and is deliberately not printed.
                problems.append(f"{group} rule {number} does not compile: "
                                f"{exc.msg} at position {exc.pos}")
                continue
            if not rule.get(reason_key):
                problems.append(f"{group} rule {number} has no stated "
                                f"{reason_key}")
            target.append((number, compiled, rule.get(reason_key, "")))
    if problems:
        print("\n".join(problems))
        sys.exit(f"{len(problems)} problem(s) in the rules from {source}")
    print(f"{len(deny)} deny and {len(allow)} allow rule(s) from {source}: "
          f"all compile and all documented")
    return deny, allow


def tracked_files() -> list[str]:
    """Every file git tracks: exactly what a push publishes."""
    out = _git("ls-files", "-z")
    if out.returncode != 0:
        sys.exit(f"git ls-files failed: {out.stderr.strip()}")
    return [p for p in out.stdout.split("\0") if p]


def scan(deny, allow, files, why_is_public: bool):
    findings = []
    skipped = []
    scanned = 0
    for rel in files:
        path = pathlib.Path(rel)
        if path.suffix.lower() in SKIP_SUFFIXES:
            skipped.append(rel)
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            skipped.append(rel)
            continue
        scanned += 1
        for line_no, line in enumerate(lines, start=1):
            for number, regex, why in deny:
                for match in regex.finditer(line):
                    hit = match.group(0)
                    if any(a.search(hit) or a.search(line)
                           for _, a, _ in allow):
                        continue
                    findings.append((rel, line_no, number, why))
                    break
    print(f"scanned {scanned} tracked text file(s), skipped {len(skipped)} "
          f"binary or undecodable")
    for rel in skipped:
        print("  not scanned:", rel)
    if not findings:
        print("no deny-list findings")
        return 0
    print()
    for rel, line_no, number, why in sorted(set(findings)):
        line = f"  {rel}:{line_no} matched deny rule {number}"
        if why_is_public and why:
            line += f" ({why})"
        print(line)
    print()
    print(f"{len(set(findings))} finding(s). The matched text is deliberately "
          f"NOT printed; open the file and line above. If a match is "
          f"expected, add an allow entry WITH A REASON to the rules. Do not "
          f"loosen a deny pattern to make this pass.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rules", default=DEFAULT_RULES_FILE,
                        help="rules file to use when no secret is set")
    parser.add_argument("--committed", choices=("true", "false"),
                        default=None,
                        help=f"override {ENV_COMMITTED}")
    args = parser.parse_args()

    committed = (args.committed == "true" if args.committed is not None
                 else _truthy(os.environ.get(ENV_COMMITTED), True))
    event = os.environ.get(ENV_EVENT, "")

    rules, source, why_is_public = load_rules(args.rules, committed, event)
    deny, allow = compile_rules(rules, source)
    return scan(deny, allow, tracked_files(), why_is_public)


if __name__ == "__main__":
    sys.exit(main())
