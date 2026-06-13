# Numeric Resolution-Marker Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Strip numeric resolution markers (`3840P`, `2160P`, `1080P/i`, `720P`, …) in `normalize_name` so they stop polluting matches — recovering the beIN Sports `4K 3840P` feed.

**Architecture:** A `RESOLUTION_PATTERNS` list applied inside the existing `if ignore_quality:` block, right after `QUALITY_PATTERNS` (before the digit/letter spacer). One regex; gated by `ignore_quality`. No other changes.

**Tech Stack:** Python stdlib `re`; pytest; existing `tests/conftest.py` fixtures (`fuzzy_module`, `matcher`).

**Design source:** `docs/specs/2026-06-13-resolution-marker-normalization-design.md`. Validated GO: 43 names changed, **0 harmful**, channel-DB **0** changed; beIN `4K 3840P` 0 → **100**.

---

## File Structure
- `Stream-Mapparr/fuzzy_matcher.py`
  - **Add** `RESOLUTION_PATTERNS` module-level list immediately after `QUALITY_PATTERNS` (ends ~line 51), before `REGIONAL_PATTERNS` (~line 54).
  - **Modify** `normalize_name`: add a `RESOLUTION_PATTERNS` loop inside the `if ignore_quality:` block, right after the `QUALITY_PATTERNS` loop.
  - **Modify** `__version__` (line 22) in the deploy task.
- `tests/test_fuzzy_matcher.py` — add a resolution-marker test section.

---

## Task 1: Resolution-marker stripping (TDD)

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py` (constant + `normalize_name`)
- Test: `tests/test_fuzzy_matcher.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_fuzzy_matcher.py`:

```python
# --------------------------------------------------------------------------- #
# Numeric resolution markers (3840P/2160P/1080P/720P...) — NNN[pi] tags that the
# keyword QUALITY_PATTERNS miss. Gated by ignore_quality (default True).
# --------------------------------------------------------------------------- #

def test_normalize_strips_3840p(matcher):
    assert matcher().normalize_name("BEIN SPORTS GOLD 3840P") == "BEIN SPORTS GOLD"


def test_normalize_strips_common_resolutions(matcher):
    m = matcher()
    assert m.normalize_name("RELAX 1 3840P") == "RELAX 1"
    assert m.normalize_name("Sky Sports 720P") == "Sky Sports"
    assert m.normalize_name("Foo 1080i") == "Foo"
    assert m.normalize_name("Foo 2160p") == "Foo"


def test_normalize_resolution_keeps_bare_numbers(matcher):
    # No p/i anchor -> NOT a resolution marker; single-digit channel numbers and bare
    # resolution-sized numbers must survive.
    m = matcher()
    assert m.normalize_name("Channel 4") == "Channel 4"
    assert m.normalize_name("Studio 1080") == "Studio 1080"


def test_normalize_resolution_no_regression(matcher):
    m = matcher()
    assert m.normalize_name("CNN HD") == "CNN"
    assert m.normalize_name("ITV1") == "ITV 1"


def test_resolution_strip_respects_ignore_quality_flag(matcher):
    # When ignore_quality=False, the resolution marker is preserved (same gate as 4K/HD).
    assert "1080" in matcher().normalize_name("Foo 1080p", ignore_quality=False)


def test_bein_4k_3840p_matches_after_resolution_fix(matcher):
    m = matcher(95)
    match, score = m.find_best_match("beIN Sports", ["### beIN " + "SP⚽RTS" + " 4K 3840P ###"])
    assert match is not None
    assert score == 100
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "3840p or common_resolutions or bein_4k or resolution" -v`
Expected: the marker tests FAIL (e.g. `normalize_name("BEIN SPORTS GOLD 3840P")` returns `"BEIN SPORTS GOLD 3840 P"`; `find_best_match` returns `(None, 0)`). The bare-number, no-regression, and `ignore_quality=False` tests already PASS.

- [ ] **Step 3a: Add the `RESOLUTION_PATTERNS` constant**

In `Stream-Mapparr/fuzzy_matcher.py`, find the end of `QUALITY_PATTERNS` and the start of `REGIONAL_PATTERNS`:

```python
    # Standalone quality tags in MIDDLE (with word boundaries on both sides)
    r'\s+\b(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)\b\s+',
]

# Regional indicator patterns: East, West, Pacific, Central, Mountain, Atlantic
REGIONAL_PATTERNS = [
```

Insert the new list between `QUALITY_PATTERNS`' closing `]` and the `# Regional indicator patterns` comment:

```python
    # Standalone quality tags in MIDDLE (with word boundaries on both sides)
    r'\s+\b(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)\b\s+',
]

# Numeric resolution markers the keyword QUALITY_PATTERNS miss: 720p, 1080p/i, 2160p,
# 3840P, 480p, etc. — any 3-4 digit run followed by p/i. The \b anchors avoid matching
# inside a longer number, and the p/i anchor keeps bare numbers (1080, "Channel 4") intact.
# Applied with re.IGNORECASE in the ignore_quality block, like QUALITY_PATTERNS.
RESOLUTION_PATTERNS = [
    r'\b\d{3,4}\s*[pi]\b',
]

# Regional indicator patterns: East, West, Pacific, Central, Mountain, Atlantic
REGIONAL_PATTERNS = [
```

- [ ] **Step 3b: Apply it in `normalize_name`**

Find the quality block (after the emoji/Unicode steps added earlier):

```python
        if ignore_quality:
            for pattern in QUALITY_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
```

Add the resolution loop right after the QUALITY loop, still inside `if ignore_quality:`:

```python
        if ignore_quality:
            for pattern in QUALITY_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
            # Strip numeric resolution markers (3840P/2160P/1080P/720P/...) before the
            # digit/letter spacer below would split "3840P" into "3840 P".
            for pattern in RESOLUTION_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
```

- [ ] **Step 4: Run the new tests**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "3840p or common_resolutions or bare_numbers or resolution or bein_4k" -v`
Expected: PASS (all).

- [ ] **Step 5: Full suite**

Run: `python -m pytest -q`
Expected: PASS — previous 167 stay green; new ~6 pass (≈173 total). If any existing test changes result, STOP and investigate.

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/fuzzy_matcher.py tests/test_fuzzy_matcher.py
git commit -m "feat(matcher): strip numeric resolution markers (3840P/720P/...) in normalize_name"
```

---

## Task 2: Version bump + deploy

- [ ] **Step 1: Confirmatory beIN check**

```bash
cat > _scratch_res_check.py <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("fm", r"Stream-Mapparr/fuzzy_matcher.py")
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
m = fm.FuzzyMatcher(plugin_dir=None, match_threshold=95)
print("beIN 4K 3840P:", m.find_best_match("beIN Sports", ["### beIN SP⚽RTS 4K 3840P ###"]))
print("collision Studio 1080:", m.normalize_name("Studio 1080"))   # must stay "Studio 1080"
PY
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _scratch_res_check.py
rm -f _scratch_res_check.py
```
Expected: a match at 100; `Studio 1080` unchanged.

- [ ] **Step 2: Bump `fuzzy_matcher.py` `__version__`** (line 22) to current calver `YY.DDD.HHMM` (UTC).

- [ ] **Step 3: Bump plugin version**

```bash
python Stream-Mapparr/bump_version.py
python scripts/check_version_sync.py
```

- [ ] **Step 4: Verify + commit**

```bash
python -m py_compile Stream-Mapparr/plugin.py Stream-Mapparr/fuzzy_matcher.py
python -m pytest -q
git add Stream-Mapparr/fuzzy_matcher.py Stream-Mapparr/plugin.py Stream-Mapparr/plugin.json
git commit -m "release: bump version for resolution-marker normalization fix"
```

- [ ] **Step 5: Deploy** via the `/deploy-plugin` procedure (copy `plugin.py`/`plugin.json`/`fuzzy_matcher.py`, `docker restart dispatcharr`, confirm the logged version, md5-verify). A fresh preview should show the beIN Sports `4K 3840P` feed now matching.

---

## Notes / out of scope
- Bare standalone numbers (`480`/`576`/`720`, 3 names) deferred. `SA …` region prefix and `beIN Sports MAX`/`AFC` (different channels) remain separate.
- `_strip_stylized_tokens`, `_normalize_emoji`, and `process_string_for_matching` are unchanged.
- Cross-port of all three matcher fixes to the sibling plugins remains deferred (root `CLAUDE.md`).
```
