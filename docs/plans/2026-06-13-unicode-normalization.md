# Stylized-Unicode Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip stylized-Unicode decoration (superscripts, small-caps, bullets) from names in `normalize_name` so streams like `RK: WEATHERNATION ᴿᴬᵂ` match channel `WeatherNation`, without harming real channel names or non-Latin scripts.

**Architecture:** Two module-level pure functions in `fuzzy_matcher.py` — `_is_decorative_char` (detects a stylized char by its Unicode *name*) and `_strip_stylized_tokens` (drops whitespace tokens that are pure decoration, then NFKD-canonicalizes the rest). One new line at the top of `normalize_name` calls the stripper before the existing ASCII tag regexes. No other behavior changes; `process_string_for_matching` is untouched.

**Tech Stack:** Python stdlib `unicodedata` (already imported), `re`; pytest; existing `tests/conftest.py` fixtures (`fuzzy_module`, `matcher`).

**Design source:** `docs/specs/2026-06-13-unicode-normalization-design.md` (rev 3). Validated GO by a read-only corpus diff: 53,992 stream names + 42,897 channel-DB names, **0 harmful** changes; WeatherNation recovers.

---

## File Structure

- `Stream-Mapparr/fuzzy_matcher.py`
  - **Add** two module-level functions after `MISC_PATTERNS` (currently ends line 108), before `class FuzzyMatcher` (line 111): `_DECORATIVE_SYMBOLS`, `_is_decorative_char`, `_strip_stylized_tokens`.
  - **Modify** `normalize_name` (line 428): insert one call after `original_name = name` (line 450), before the `if ignore_quality:` block (line 457).
  - **Modify** `__version__` (line 22) to a fresh calver in the deploy task.
- `tests/test_fuzzy_matcher.py`
  - **Add** a new test section with direct helper tests + `normalize_name` integration tests + no-regression cases. New cases go under the existing `normalize_name — tag stripping` banner area.

Self-contained, single-responsibility: the two helpers are pure (no `self`, no I/O), live next to the existing `QUALITY_PATTERNS`/`MISC_PATTERNS` module constants, and are independently unit-testable via the `fuzzy_module` fixture.

---

## Task 1: Decoration-detection helpers

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py` (insert after line 108, before `class FuzzyMatcher` at line 111)
- Test: `tests/test_fuzzy_matcher.py` (new section)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fuzzy_matcher.py`. Use literal Unicode chars (UTF-8 source), matching the existing convention in this file (`test_accents_folded` uses `'Canalé'`, `test_unicode_user_ignore_tag_does_not_crash` uses `'┃NLZIET┃'`); the comment block documents each marker's code points so they stay unambiguous.

```python
# --------------------------------------------------------------------------- #
# Stylized-Unicode decoration stripping (WeatherNation fix).
# Markers are built from code points so the file stays ASCII and unambiguous.
#   RAW = superscript R A W;  HD = superscript H D;  FHD = small-cap F H D
#   FISH = FISHEYE bullet;  FPS60 = superscript "60fps";  VIP = superscript V I P
# --------------------------------------------------------------------------- #
RAW = "ᴿᴬᵂ"
HD = "ᴴᴰ"
FHD = "ꜰʜᴅ"
FISH = "◉"
FPS60 = "⁶⁰ᶠᵖˢ"
VIP = "ⱽᴵᴾ"
ARABIC = "الم"               # 3 Arabic letters
CYRILLIC = "Россия"  # "Rossiya" in Cyrillic


def test_is_decorative_char_classifies_markers(fuzzy_module):
    f = fuzzy_module._is_decorative_char
    assert f("ᴿ") is True   # superscript modifier-letter R
    assert f("ꜰ") is True   # Latin small capital F (IPA/Latin-Ext-D)
    assert f("◉") is True   # FISHEYE bullet (curated)
    assert f("⁶") is True   # superscript six


def test_is_decorative_char_keeps_real_letters(fuzzy_module):
    f = fuzzy_module._is_decorative_char
    assert f("G") is False       # ASCII
    assert f("4") is False       # ASCII digit
    assert f("я") is False  # Cyrillic small ya
    assert f("ا") is False  # Arabic alef


def test_strip_stylized_tokens_drops_superscript_token(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("WEATHERNATION " + RAW) == "WEATHERNATION"


def test_strip_stylized_tokens_drops_punct_glued_ornament(fuzzy_module):
    # "◉: CNN" -> the "◉:" token is decoration glued to ASCII ':' -> dropped
    assert fuzzy_module._strip_stylized_tokens(FISH + ": CNN") == "CNN"


def test_strip_stylized_tokens_keeps_ascii_tier_word(fuzzy_module):
    # collision guard: ASCII "Gold" survives; only the superscript token goes
    assert fuzzy_module._strip_stylized_tokens("Gold " + RAW) == "Gold"


def test_strip_stylized_tokens_is_non_latin_safe(fuzzy_module):
    # glued Arabic + superscript HD: the Arabic token is KEPT (not all decorative);
    # NFKD folds the trailing superscript HD to ASCII "HD".
    assert fuzzy_module._strip_stylized_tokens(ARABIC + HD) == ARABIC + "HD"


def test_strip_stylized_tokens_ascii_fast_path(fuzzy_module):
    assert fuzzy_module._strip_stylized_tokens("Fox Sports 1") == "Fox Sports 1"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "decorative or stylized" -v`
Expected: FAIL with `AttributeError: module ... has no attribute '_is_decorative_char'` (and `_strip_stylized_tokens`).

- [ ] **Step 3: Implement the helpers**

In `Stream-Mapparr/fuzzy_matcher.py`, insert after `MISC_PATTERNS` (line 108) and before `class FuzzyMatcher` (line 111). `unicodedata` is already imported at line 11.

```python
# --------------------------------------------------------------------------- #
# Stylized-Unicode decoration stripping
# --------------------------------------------------------------------------- #
# Streams tag names with stylized-Unicode tier/format markers ("WEATHERNATION ᴿᴬᵂ",
# "... ꜰʜᴅ", "◉: CNN") that the ASCII tag regexes below cannot see. We drop whole
# tokens that are pure decoration BEFORE the ASCII pipeline runs. Detection is by
# Unicode character *name* (not code-point ranges), so it covers superscripts,
# "modifier letter" superscript capitals, and Latin small-caps wherever they live
# (e.g. small-cap H is U+029C in IPA Extensions, modifier V is U+2C7D in Latin-Ext-C,
# both outside the obvious blocks).

# Ornament glyphs whose Unicode name carries no decoration keyword.
_DECORATIVE_SYMBOLS = frozenset("◉")  # FISHEYE


def _is_decorative_char(ch):
    """True for a stylized letterform/ornament that carries no semantic content in a
    channel name (superscripts, subscripts, modifier-letter superscript capitals,
    Latin small-capitals, curated bullets). ASCII and ordinary letters return False."""
    if ch.isascii():
        return False
    if ch in _DECORATIVE_SYMBOLS:
        return True
    try:
        nm = unicodedata.name(ch)
    except ValueError:
        return False
    return ('SUPERSCRIPT' in nm or 'SUBSCRIPT' in nm
            or 'SMALL CAPITAL' in nm or 'MODIFIER LETTER' in nm)


def _strip_stylized_tokens(name):
    """Drop whitespace tokens that are pure stylized decoration, then NFKD-canonicalize
    the remainder. A token is decoration when it has >=1 decorative char, no ASCII
    alphanumeric, and every char is decorative or ASCII punctuation (so "◉:" and
    "ᴴᴰ/ᴿᴬᵂ" are dropped too). Real ASCII words (Gold/VIP) and non-Latin letters
    (Arabic/Cyrillic/CJK) are always kept. ASCII-only input is returned unchanged
    (ASCII is invariant under both steps, keeping the common case allocation-cheap)."""
    if name.isascii():
        return name
    kept = []
    for tok in name.split():
        has_decorative = any(_is_decorative_char(c) for c in tok)
        has_ascii_alnum = any(c.isascii() and c.isalnum() for c in tok)
        only_decorative_or_punct = all(
            _is_decorative_char(c) or (c.isascii() and not c.isalnum()) for c in tok
        )
        if has_decorative and only_decorative_or_punct and not has_ascii_alnum:
            continue  # pure decoration -> drop the whole token
        kept.append(tok)
    return unicodedata.normalize('NFKD', ' '.join(kept))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "decorative or stylized" -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add Stream-Mapparr/fuzzy_matcher.py tests/test_fuzzy_matcher.py
git commit -m "feat(matcher): add stylized-Unicode decoration detectors"
```

---

## Task 2: Wire the stripper into `normalize_name`

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py:450` (inside `normalize_name`)
- Test: `tests/test_fuzzy_matcher.py` (integration cases)

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_fuzzy_matcher.py` (reuses the `RAW`/`HD`/`FHD`/`FISH`/`FPS60`/`CYRILLIC` constants from Task 1). `normalize_name` does NOT lowercase, so expected outputs preserve case.

```python
def test_normalize_recovers_weathernation(matcher):
    # The real WeatherNation stream: "RK: WEATHERNATION <superscript RAW>".
    # "RK:" is a geographic prefix (already stripped); the new step removes RAW.
    assert matcher().normalize_name("WEATHERNATION " + RAW) == "WEATHERNATION"


def test_normalize_strips_superscript_hd(matcher):
    assert matcher().normalize_name("C-SPAN2 " + HD) == "C SPAN 2"


def test_normalize_strips_small_caps(matcher):
    # NFKD alone does NOT fold small-caps; the token strip is what removes ꜰʜᴅ.
    assert matcher().normalize_name("ESPN " + FHD) == "ESPN"


def test_normalize_strips_punct_glued_bullet(matcher):
    assert matcher().normalize_name(FISH + ": CNN") == "CNN"


def test_normalize_strips_glued_and_standalone_markers(matcher):
    name = "ENTERTAINMENT " + HD + "/" + RAW + " " + FPS60
    assert matcher().normalize_name(name) == "ENTERTAINMENT"


def test_normalize_collision_guard_keeps_ascii_tier_words(matcher):
    m = matcher()
    assert m.normalize_name("Gold") == "Gold"
    assert m.normalize_name("VIP") == "VIP"


def test_normalize_preserves_non_latin(matcher):
    assert matcher().normalize_name(CYRILLIC) == CYRILLIC


def test_normalize_no_regression_plain_ascii(matcher):
    m = matcher()
    assert m.normalize_name("Fox Sports 1") == "Fox Sports 1"
    assert m.normalize_name("Fox Sports 2") == "Fox Sports 2"
    assert m.normalize_name("CNN HD") == "CNN"
    assert m.normalize_name("ITV1") == "ITV 1"
```

- [ ] **Step 2: Run to verify the new behavior tests fail (and ASCII ones already pass)**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "normalize_recovers or normalize_strips or normalize_collision or preserves_non_latin" -v`
Expected: the marker-stripping tests FAIL (output still contains the superscript/small-cap/bullet, e.g. `'WEATHERNATION ᴿᴬᵂ' != 'WEATHERNATION'`). `test_normalize_no_regression_plain_ascii` PASSES already (no change for ASCII).

- [ ] **Step 3: Insert the call into `normalize_name`**

In `Stream-Mapparr/fuzzy_matcher.py`, immediately after `original_name = name` (line 450) and before the `# CRITICAL FIX (v25.019.0100)` comment / `if ignore_quality:` block (line 452/457):

```python
        # Store original for logging
        original_name = name

        # Strip stylized-Unicode decoration (superscript/small-cap tier markers,
        # bullets) up front so the ASCII tag regexes below see plain text. Runs
        # unconditionally: a token written in superscript/small-caps is decoration
        # regardless of tag_handling, and it would otherwise block matches
        # (e.g. "WEATHERNATION ᴿᴬᵂ" never matches channel "WeatherNation").
        name = _strip_stylized_tokens(name)

```

(Leave the existing `if ignore_quality:` block exactly as-is right below it.)

- [ ] **Step 4: Run the integration tests to verify they pass**

Run: `python -m pytest tests/test_fuzzy_matcher.py -k "normalize_recovers or normalize_strips or normalize_collision or preserves_non_latin or no_regression" -v`
Expected: PASS (all 8).

- [ ] **Step 5: Run the FULL suite to confirm no regression**

Run: `python -m pytest -q`
Expected: PASS — the previous 139 tests stay green; the new ~15 cases pass (≈154 total). If any existing matcher test changes result, STOP and investigate (a real channel name must not be altered).

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/fuzzy_matcher.py tests/test_fuzzy_matcher.py
git commit -m "feat(matcher): strip stylized-Unicode decoration in normalize_name (WeatherNation fix)"
```

---

## Task 3: Corpus-diff re-confirmation, version bump, deploy

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py:22` (`__version__`)
- Modify: `Stream-Mapparr/plugin.py` + `Stream-Mapparr/plugin.json` (via `bump_version.py`)

- [ ] **Step 1: Re-run the corpus-diff gate against the real `normalize_name` (now that the code is live)**

Write a temporary script that imports the *modified* `fuzzy_matcher.py` and diffs `normalize_name` over the real corpus and channel DBs; confirm the production code matches the recon prototype.

```bash
cat > _scratch_corpus_check.py <<'PY'
import importlib.util, unicodedata
spec = importlib.util.spec_from_file_location("fm", r"Stream-Mapparr/fuzzy_matcher.py")
fm = importlib.util.module_from_spec(spec); spec.loader.exec_module(fm)
m = fm.FuzzyMatcher(plugin_dir=None)
corpus = r"C:\Users\User\docker\Lineuparr\_stream_exports\streamq_names.txt"
changed = total = 0
wn = None
with open(corpus, encoding="utf-8") as fh:
    for line in fh:
        name = line.rstrip("\n")
        if not name:
            continue
        total += 1
        out = m.normalize_name(name)
        if "WEATHERNATION" in name.upper() and wn is None:
            wn = (name, out)   # WeatherNation spot-check
        if out != name:
            changed += 1
print(f"corpus={total} changed={changed}")
print("weathernation:", wn)
PY
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python _scratch_corpus_check.py
rm -f _scratch_corpus_check.py
```

Expected: `changed` ≈ 18,846; the WeatherNation line normalizes without its superscript token. (This is a sanity re-confirmation of the recon GO; the authoritative safety check is the green test suite + the recon's harmful=0 result already recorded in the spec.) If `corpus` reads 0, the corpus file path differs — skip this step (it is confirmatory only) and rely on the test suite.

- [ ] **Step 2: Bump `fuzzy_matcher.py` `__version__`**

Edit line 22 to the current calver `YY.DDD.HHMM` (UTC), e.g. matching today (day 164):

```python
__version__ = "26.164.HHMM"   # set HHMM to current UTC time
```

- [ ] **Step 3: Bump the plugin version (keeps `plugin.py` + `plugin.json` in sync)**

```bash
python Stream-Mapparr/bump_version.py
python scripts/check_version_sync.py
```

- [ ] **Step 4: Verify + commit**

```bash
python -m py_compile Stream-Mapparr/plugin.py Stream-Mapparr/fuzzy_matcher.py
python -m pytest -q
git add Stream-Mapparr/fuzzy_matcher.py Stream-Mapparr/plugin.py Stream-Mapparr/plugin.json
git commit -m "release: bump version for stylized-Unicode normalization fix"
```

- [ ] **Step 5: Deploy**

Use the **`/deploy-plugin`** skill (copy `plugin.py`, `plugin.json`, `fuzzy_matcher.py` into the container, `docker restart dispatcharr`, confirm the logged version matches). `fuzzy_matcher.py` is a changed module, so it MUST be copied. After reload, run a dry-run preview and confirm a WeatherNation-style stream now matches.

---

## Task 4 (OPTIONAL — confirm with user before doing): Port to sibling plugins

The workspace root `CLAUDE.md` says: "Matcher fix? `fuzzy_matcher.py` is copy-pasted across 4 plugins and drifting. Port the fix + its regression test to all copies until the shared-core refactor lands." This fix qualifies.

**Scope:** Lineuparr + the two other plugins that embed `fuzzy_matcher.py`. Each is a **separate repo** and may have a drifted `normalize_name`, so this is NOT a blind copy.

**Per sibling (only if the user opts in):**
- [ ] Locate that plugin's `fuzzy_matcher.py` and confirm `normalize_name` has the same shape (an `original_name = name` line before the ASCII tag regexes; `unicodedata` imported).
- [ ] Add the same `_DECORATIVE_SYMBOLS` / `_is_decorative_char` / `_strip_stylized_tokens` helpers.
- [ ] Insert the `name = _strip_stylized_tokens(name)` call at the matching point.
- [ ] Port the regression tests (adapt fixtures to that repo's test harness).
- [ ] Run that repo's suite, bump its version, deploy per its own deploy doc.

> Surface this at the execution-choice gate. Do NOT auto-expand the Stream-Mapparr task into three other repos without explicit user consent.

---

## Notes / out of scope
- **`SP⚽RTS` emoji-letter substitution (681 names)** is explicitly deferred (spec rev 3) — needs a separate emoji→letter brainstorm.
- `process_string_for_matching` is intentionally unchanged.
- No new settings, no UI change, no schema change.
