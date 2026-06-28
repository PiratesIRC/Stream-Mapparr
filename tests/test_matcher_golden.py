"""Golden drift gate for the pure matcher primitives (Stage 0).

Freezes the output of fuzzy_matcher.py's PURE primitives (normalize_name,
process_string_for_matching, calculate_similarity, extract_callsign,
normalize_callsign) over a shared corpus, so any unreviewed change to match behavior
fails CI. During the matcher-standardization migration, an INTENDED de-drift change is
landed by re-running ``python tools/matcher_parity_check.py --write`` at the workspace
root and committing the updated ``matcher_golden_baseline.json`` in the same change —
never by editing expectations ad hoc.

This file is intentionally self-contained and IDENTICAL across Stream-Mapparr,
Channel-Maparr, and EPG-Janitor (CI runs per-repo and cannot see the workspace tool); it
loads fuzzy_matcher.py directly from the inner folder, so it needs no conftest fixture.
Only the committed baseline JSON differs per plugin. Keep the corpus below in lockstep
with tools/matcher_parity_check.py. See MATCHER-STANDARDIZATION-PLAN.md §7.
"""
import importlib.util
import json
import os
import sys

import pytest

# --- shared corpus (keep identical to tools/matcher_parity_check.py) ---------
NAMES = [
    "US: USA Network HD", "US| ESPN", "[US] CNN", "UK: BBC One", "UK| ITV 1",
    "Discovery Channel 4K", "HBO HD", "ESPN [FHD]", "Cinemax HD", "TNT UHD RAW",
    "BBC Three", "BBC Four", "Three Angels Broadcasting Network", "ESPN 2", "HBO 2",
    "JusticeCentral.TV", "DangerTV", "NewsNation",
    "HBO East", "HBO West", "HBO (W)", "Fox Sports West", "ESPN Pacific",
    "(PRIME) FOX News", "(D1) CBS",
    "Disney+", "Discovery+", "Paramount+", "Disney Channel", "Discovery Channel",
    "Justice Central", "Justice Central.TV", "Justice Central TV", "True Crime Network",
    "WABC-TV", "KCBS", "KING 5", "WAVE 3", "WOOD TV8", "WHO 13", "KOMO News",
    "\U0001f174\U0001f182\U0001f17f\U0001f175", "┃US┃ ESPN", "★ CNN ★",
    "Россия 1", "France 2", "beИN SPORTS",
    "HLN", "MTV", "getTV", "TUDN", "SEC Network", "NHL Network", "BBC News",
]
PAIRS = [
    ("usanetwork", "usanetwork"), ("espn", "espn2"), ("hbo", "hbo2"),
    ("bbcone", "bbctwo"), ("disney", "disneyplus"), ("foxnews", "foxnews"),
    ("cnn", "cnninternational"), ("discoverychannel", "discovery"), ("e", "ae"),
    ("paramount", "paramountnetwork"), ("nflnetwork", "nhlnetwork"),
    ("justicecentral", "truecrimenetwork"), ("a", "a"), ("", ""),
]
FLAG_COMBOS = [
    ("all_on", dict(ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True)),
    ("regional_off", dict(ignore_quality=True, ignore_regional=False, ignore_geographic=True, ignore_misc=True)),
]


def _safe(fn, *args, **kwargs):
    try:
        val = fn(*args, **kwargs)
    except Exception as exc:
        return f"__ERROR__: {type(exc).__name__}: {exc}"
    if isinstance(val, float):
        return round(val, 9)
    return val


def run_corpus(matcher):
    out = {
        "process_string": {n: _safe(matcher.process_string_for_matching, n) for n in NAMES},
        "normalize_name": {},
        "calculate_similarity": {f"{a}|{b}": _safe(matcher.calculate_similarity, a, b) for a, b in PAIRS},
        "extract_callsign": {n: _safe(matcher.extract_callsign, n) for n in NAMES},
        "normalize_callsign": {n: _safe(matcher.normalize_callsign, n) for n in NAMES},
    }
    for label, flags in FLAG_COMBOS:
        out["normalize_name"][label] = {n: _safe(matcher.normalize_name, n, **flags) for n in NAMES}
    return out


def _flatten(d, prefix=""):
    for k in sorted(d):
        v = d[k]
        if isinstance(v, dict):
            yield from _flatten(v, f"{prefix}{k}.")
        else:
            yield (f"{prefix}{k}", v)


def _load_fuzzy_matcher():
    """Load fuzzy_matcher.py from the inner folder, conftest-free.

    Layout: <repo>/tests/test_matcher_golden.py and <repo>/<repo_name>/fuzzy_matcher.py.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    inner = next(
        (os.path.join(repo_root, _e) for _e in sorted(os.listdir(repo_root))
         if os.path.isfile(os.path.join(repo_root, _e, "fuzzy_matcher.py"))),
        os.path.join(repo_root, os.path.basename(repo_root)),
    )
    path = os.path.join(inner, "fuzzy_matcher.py")
    saved_path = list(sys.path)
    saved_aliases = sys.modules.pop("aliases", None)
    sys.path.insert(0, inner)
    try:
        spec = importlib.util.spec_from_file_location("fm_golden_under_test", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved_path
        sys.modules.pop("aliases", None)
        if saved_aliases is not None:
            sys.modules["aliases"] = saved_aliases
    return mod


_FM_MOD = _load_fuzzy_matcher()

_BASELINE_FILE = os.path.join(os.path.dirname(__file__), "matcher_golden_baseline.json")
with open(_BASELINE_FILE, encoding="utf-8") as _fh:
    _BASELINE_FLAT = dict(_flatten(json.load(_fh)))


@pytest.fixture(scope="module")
def current():
    return dict(_flatten(run_corpus(_FM_MOD.FuzzyMatcher())))


@pytest.mark.parametrize("key", sorted(_BASELINE_FLAT))
def test_primitive_matches_golden(current, key):
    assert current.get(key, "<missing>") == _BASELINE_FLAT[key], (
        f"matcher primitive drifted at {key!r}: "
        f"baseline={_BASELINE_FLAT[key]!r} now={current.get(key, '<missing>')!r}. "
        f"If intended, re-run tools/matcher_parity_check.py --write and commit the baseline."
    )
