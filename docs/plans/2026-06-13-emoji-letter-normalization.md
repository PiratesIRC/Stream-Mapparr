# Emoji-as-letter Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Map emoji used as letters (`⚽`→`o` in `SP⚽RTS`=SPORTS) and strip emoji decoration in `normalize_name`, so the `beIN Sports` channel matches (currently 0).

**Architecture:** A module-level pure helper `_normalize_emoji` in `fuzzy_matcher.py` + a glyph→letter map + an ornament set. One new line at the top of `normalize_name` calls it before `_strip_stylized_tokens`. No other behavior changes.

**Tech Stack:** Python stdlib `re` (already imported); pytest; existing `tests/conftest.py` fixtures (`fuzzy_module`, `matcher`).

**Design source:** `docs/specs/2026-06-13-emoji-letter-normalization-design.md`. Validated GO: 695 names emoji-cleaned, **0 harmful**, channel-DB **0** changed; `find_best_match("beIN Sports", ["### beIN SP⚽RTS ###"])` 0 → **100**.

---

## File Structure

- `Stream-Mapparr/fuzzy_matcher.py`
  - **Add** `_EMOJI_LETTER_MAP`, `_EMOJI_ORNAMENTS`, `_ZERO_WIDTH`, and `_normalize_emoji` immediately after `_strip_stylized_tokens` (ends ~line 161), before `class FuzzyMatcher:` (~line 164).
  - **Modify** `normalize_name`: insert `name = _normalize_emoji(name)` after `original_name = name` (~line 450) and before the `# Strip stylized-Unicode decoration` comment / `name = _strip_stylized_tokens(name)` line.
  - **Modify** `__version__` (line 22) in the deploy task.
- `tests/test_fuzzy_matcher.py`
  - **Add** a new section: direct `_normalize_emoji` tests + `normalize_name` integration + an end-to-end `find_best_match` test + no-regression cases.

---

## Task 1: `_normalize_emoji` helper

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py` (insert before `class FuzzyMatcher:`)
- Test: `tests/test_fuzzy_matcher.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fuzzy_matcher.py` (literal Unicode, matching the file's existing convention):

```python
# --------------------------------------------------------------------------- #
# Emoji-as-letter normalization (beIN SP⚽RTS = SPORTS) + emoji decoration.
#   BALL = ⚽ SOCCER BALL (U+26BD) used as the letter 'o' mid-word
#   VS16 = U+FE0F zero-width variation selector (invisible noise)
# --------------------------------------------------------------------------- #
BALL = "⚽"
VS16 = "️"  # U+FE0F


def test_normalize_emoji_ball_midword_to_o(fuzzy_module):
    f = fuzzy_module._normalize_emoji
    assert f("SP" + BALL + "RTS") == "SPoRTS"
    assert f("Sp" + BALL + "rts") == "Sports"


def test_normalize_emoji_edge_ball_stripped_not_mapped(fuzzy_module):
    # ball NOT flanked by two ASCII letters -> dropped, not turned into 'o'
    assert fuzzy_module._normalize_emoji("BE" + BALL) == "BE"


def test_normalize_emoji_strips_zero_width(fuzzy_module):
    assert fuzzy_module._normalize_emoji(VS16 + "HULU") == "HULU"


def test_normalize_emoji_ascii_fast_path(fuzzy_module):
    assert fuzzy_module._normalize_emoji("Fox Sports") == "Fox Sports"


def test_normalize_emoji_leaves_non_emoji_nonascii(fuzzy_module):
    # no emoji present -> returned unchanged (no NFKD here; that's _strip_stylized_tokens)
    assert fuzzy_module._normalize_emoji("Россия") == "Россия"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "normalize_emoji" -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_normalize_emoji'`.

- [ ] **Step 3: Implement the helper**

In `Stream-Mapparr/fuzzy_matcher.py`, insert immediately before `class FuzzyMatcher:` (after `_strip_stylized_tokens`). `re` is already imported (line 8).

```python
# --------------------------------------------------------------------------- #
# Emoji-as-letter + emoji decoration normalization
# --------------------------------------------------------------------------- #
# Some streams use an emoji AS A LETTER inside a word: "SP⚽RTS" / "Sp⚽rts" where the
# soccer ball stands in for 'o' (= SPORTS, the beIN family). _strip_stylized_tokens keeps
# the token (it has ASCII alnum) and process_string_for_matching would turn the ball into a
# space ("sp rts"), so it never matches "sports". We substitute the glyph for the letter it
# replaces (only when flanked by ASCII letters) and strip emoji used purely as decoration.

# Emoji that visually replace an ASCII letter when embedded in a word. Extensible.
_EMOJI_LETTER_MAP = {'⚽': 'o'}            # SOCCER BALL = 'o'  (SP⚽RTS -> SPORTS)
# Pictographic ornaments to delete (incl. a stray/standalone ball not used as a letter).
_EMOJI_ORNAMENTS = frozenset('♬☾⚽')       # beamed notes, last-quarter moon, soccer ball
# Zero-width / invisible code points that only add noise to a name.
_ZERO_WIDTH = ('️', '‍')         # VARIATION SELECTOR-16, ZERO WIDTH JOINER


def _normalize_emoji(name):
    """Map emoji-as-letters to their letter and strip emoji decoration.

    The letter substitution fires ONLY when the glyph is flanked by ASCII letters
    (so "SP⚽RTS" -> "SPoRTS" but a standalone/edge "⚽" is treated as decoration and
    dropped). Zero-width selectors and ornament pictographs are deleted outright.
    ASCII-only input is returned unchanged (no emoji possible)."""
    if name.isascii():
        return name
    for zw in _ZERO_WIDTH:
        if zw in name:
            name = name.replace(zw, '')
    for glyph, letter in _EMOJI_LETTER_MAP.items():
        if glyph in name:
            name = re.sub(r'(?<=[A-Za-z])' + re.escape(glyph) + r'(?=[A-Za-z])', letter, name)
    if any(c in _EMOJI_ORNAMENTS for c in name):
        name = ''.join(c for c in name if c not in _EMOJI_ORNAMENTS)
    return name
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "normalize_emoji" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add Stream-Mapparr/fuzzy_matcher.py tests/test_fuzzy_matcher.py
git commit -m "feat(matcher): add emoji-as-letter + emoji decoration normalizer"
```

---

## Task 2: Wire into `normalize_name` + end-to-end

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py:~450` (inside `normalize_name`)
- Test: `tests/test_fuzzy_matcher.py`

- [ ] **Step 1: Write the failing integration + e2e tests**

Append to `tests/test_fuzzy_matcher.py` (reuses `BALL` from Task 1):

```python
def test_normalize_name_ball_to_sports(matcher):
    m = matcher()
    assert m.normalize_name("SP" + BALL + "RTS") == "SPoRTS"
    assert m.normalize_name("Sp" + BALL + "rts") == "Sports"


def test_normalize_name_strips_standalone_ball(matcher):
    assert matcher().normalize_name("UEFA CHAMPIONS LEAGUE " + BALL) == "UEFA CHAMPIONS LEAGUE"


def test_normalize_name_strips_music_notes(matcher):
    # ♬ notes stripped; the existing trailing " TV" suffix rule then drops TV.
    assert matcher().normalize_name("♬♬♬ MUSIC TV ♬♬♬") == "MUSIC"


def test_normalize_name_emoji_collision_and_non_latin_guards(matcher):
    m = matcher()
    assert m.normalize_name("Gold") == "Gold"
    assert m.normalize_name("Россия") == "Россия"


def test_normalize_name_emoji_no_regression(matcher):
    m = matcher()
    assert m.normalize_name("Fox Sports 1") == "Fox Sports 1"
    assert m.normalize_name("CNN HD") == "CNN"


def test_bein_sports_matches_after_emoji_fix(matcher):
    # The headline win: beIN SP⚽RTS now matches channel "beIN Sports" at strict threshold.
    m = matcher(95)
    match, score = m.find_best_match("beIN Sports", ["### beIN " + "SP" + BALL + "RTS" + " ###"])
    assert match is not None
    assert score == 100
```

- [ ] **Step 2: Run to verify the new-behavior tests fail**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "ball_to_sports or standalone_ball or music_notes or bein_sports" -v`
Expected: the emoji tests FAIL (e.g. `normalize_name("SP⚽RTS")` still returns `"SP⚽RTS"`; `find_best_match` returns `(None, 0)`). The collision/no-regression tests already PASS.

- [ ] **Step 3: Insert the call into `normalize_name`**

In `Stream-Mapparr/fuzzy_matcher.py`, find this exact block (added by the Unicode fix):

```python
        # Store original for logging
        original_name = name

        # Strip stylized-Unicode decoration (superscript/small-cap tier markers,
```

Insert the emoji call between `original_name = name` and the `# Strip stylized-Unicode decoration` comment, so it becomes:

```python
        # Store original for logging
        original_name = name

        # Map emoji-as-letters (⚽ = 'o' in "SP⚽RTS") and strip emoji decoration, before
        # the stylized-Unicode strip and ASCII regexes below — so "beIN SP⚽RTS" -> "beIN sports".
        name = _normalize_emoji(name)

        # Strip stylized-Unicode decoration (superscript/small-cap tier markers,
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "ball_to_sports or standalone_ball or music_notes or collision_and_non_latin or emoji_no_regression or bein_sports" -v`
Expected: PASS (all).

- [ ] **Step 5: Run the FULL suite**

Run: `python -m pytest -q`
Expected: PASS — the previous 155 tests stay green; new ~11 cases pass (≈166 total). If any existing test changes result, STOP and investigate.

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/fuzzy_matcher.py tests/test_fuzzy_matcher.py
git commit -m "feat(matcher): substitute emoji-as-letters in normalize_name (beIN Sports fix)"
```

---

## Task 3: Version bump + deploy

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py:22` (`__version__`)
- Modify: `Stream-Mapparr/plugin.py` + `plugin.json` (via `bump_version.py`)

- [ ] **Step 1: Confirmatory corpus + beIN check against the live code**

```bash
cat > _scratch_emoji_check.py <<'PY'
import importlib.util
spec = importlib.util.spec_from_file_location("fm", r"Stream-Mapparr/fuzzy_matcher.py")
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
m = fm.FuzzyMatcher(plugin_dir=None, match_threshold=95)
match, score = m.find_best_match("beIN Sports", ["### beIN SP⚽RTS ###"])
print("beIN base feed:", match, score)   # expect a match @ 100
PY
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _scratch_emoji_check.py
rm -f _scratch_emoji_check.py
```
Expected: a non-None match at score 100.

- [ ] **Step 2: Bump `fuzzy_matcher.py` `__version__`** (line 22) to the current calver `YY.DDD.HHMM` (UTC).

- [ ] **Step 3: Bump the plugin version**

```bash
python Stream-Mapparr/bump_version.py
python scripts/check_version_sync.py
```

- [ ] **Step 4: Verify + commit**

```bash
python -m py_compile Stream-Mapparr/plugin.py Stream-Mapparr/fuzzy_matcher.py
python -m pytest -q
git add Stream-Mapparr/fuzzy_matcher.py Stream-Mapparr/plugin.py Stream-Mapparr/plugin.json
git commit -m "release: bump version for emoji-as-letter normalization fix"
```

- [ ] **Step 5: Deploy** via the `/deploy-plugin` procedure (copy `plugin.py`/`plugin.json`/`fuzzy_matcher.py`, `docker restart dispatcharr`, confirm the logged version). After reload, a preview should show `beIN Sports` move from 0 to ≥1 match.

---

## Notes / out of scope
- The fix recovers the **base `beIN Sports` feed**. It does NOT fix variants failing for non-emoji reasons:
  `SA …` region prefix, `… 4K 3840P` resolution noise, and the genuinely-different `beIN Sports MAX`/`AFC`
  sub-channels. Those (esp. stripping a bare `3840P` resolution marker) are a separate normalization effort.
- `_strip_stylized_tokens` and `process_string_for_matching` are unchanged.
- Cross-port to the 3 sibling plugins remains deferred (root `CLAUDE.md` rule), same as the Unicode fix.
```
