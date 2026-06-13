# Design: Emoji-as-letter + emoji decoration normalization

**Date:** 2026-06-13
**Status:** Approved (recon-validated; user chose module-level map + include decoration stripping). Ready for plan.
**Component:** `Stream-Mapparr/fuzzy_matcher.py` (`normalize_name`)
**Related:** follows the stylized-Unicode fix (`2026-06-13-unicode-normalization-design.md`, v1.26.1642109).

## Problem

Some streams use an **emoji as a letter inside a word**: `SP⚽RTS` / `Sp⚽rts` (the `⚽` SOCCER BALL
U+26BD replaces the letter "o" = **SPORTS**, the beIN family). `normalize_name` keeps the token (it
has ASCII alnum) and `process_string_for_matching` turns `⚽` into a space, yielding `sp rts` — which
never matches `sports`. Result: the **`beIN Sports`** channel gets **0 matches** in production.

A few other pictographs appear as pure decoration (`♬` notes, `☾` moon, standalone `⚽`) plus the
zero-width `U+FE0F`/ZWJ, which leak into normalized names.

## Recon (real corpus, 53,992 names)

After excluding the stylized decoration already handled by `_strip_stylized_tokens`, only **4 distinct
pictographs** exist in the entire corpus:

| glyph | U+ | role | count | handling |
|---|---|---|---|---|
| `⚽` SOCCER BALL | 26BD | **letter "o"** mid-word (`SP⚽RTS`/`Sp⚽rts`) | 682 | map → `o` |
| `⚽` (standalone/edge) | 26BD | decoration | 6 | strip |
| `♬` BEAMED 16TH NOTES | 266C | decoration | 4 | strip |
| `☾` LAST QUARTER MOON | 263E | decoration | 1 | strip |
| `️`/ZWJ | FE0F / 200D | zero-width noise | 3 | strip |

No hearts/stars/other letter-emoji exist. The emoji-as-letter problem is, in practice, exactly one
glyph → one letter.

## Approach (chosen): `_normalize_emoji`, applied at the top of `normalize_name`

A module-level helper, inserted **before** `_strip_stylized_tokens` (which itself runs before the ASCII
tag regexes), so every downstream stage (quality strip, token sort, no-space cache, alias lookup) sees
corrected text — exactly how `_strip_stylized_tokens` was integrated.

```python
# Emoji that visually replace an ASCII letter when embedded in a word.
_EMOJI_LETTER_MAP = {'⚽': 'o'}            # SOCCER BALL = 'o'  (SP⚽RTS -> SPORTS)
# Pictographic ornaments to delete (incl. a stray/standalone ball not used as a letter).
_EMOJI_ORNAMENTS = frozenset('♬☾⚽')
_ZERO_WIDTH = ('️', '‍')         # variation selector-16, ZWJ -> invisible

def _normalize_emoji(name):
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

- **Map keyed by glyph, module-level** (user decision) — consistent with `_DECORATIVE_SYMBOLS`. Extensible:
  add `glyph: letter` pairs as new cases appear.
- **Mid-word only:** the `⚽→o` substitution fires **only when flanked by ASCII letters** (`(?<=[A-Za-z])…(?=[A-Za-z])`),
  so `SP⚽RTS`→`SPoRTS`. A standalone/edge `⚽` (e.g. `UEFA CHAMPIONS LEAGUE ⚽`) is **not** turned into a
  stray "o" — it falls through to the ornament strip and is removed.
- **Decoration stripping** (user decision): zero-width selectors and the ornament pictographs are deleted.
- Case: the inserted `o` is lowercase; all comparison paths lowercase downstream, so `SPoRTS`→`sports`.

## What does NOT change
Match pipeline order, thresholds, the numeric-sibling guard, the ASCII tag regexes, `_strip_stylized_tokens`,
and `process_string_for_matching`. This is purely additional input normalization.

## Validation (recon, GO)

Old-vs-new `normalize_name` (faithful prototype: prepend `_normalize_emoji` to the input) over the real corpus
and all channel DBs:

| Set | Compared | Changed by emoji step | Harmful |
|---|---|---|---|
| `streamq_names.txt` | 53,992 | 695 | **0** (every changed name contained one of `⚽♬☾`/FE0F/ZWJ) |
| All `*_channels.json` `channel_name` | 42,897 | **0** | 0 |

Real-matcher check (`find_best_match("beIN Sports", …)`, thresholds 95/85/80):

| stream | old | new |
|---|---|---|
| `### beIN SP⚽RTS ###` | no match | **100** |
| `### beIN SP⚽RTS ᴿᴬᵂ ###` | no match | **100** |

## Scope — out (explicit)
The emoji fix recovers the **base `beIN Sports` feed**. It does **not** fix beIN variants that fail for
*non-emoji* reasons, and those are out of scope here:
- `### SA beIN SP⚽RTS SD ###` — leading `SA` region token (geographic-prefix normalization gap).
- `### beIN SP⚽RTS 4K 3840P ###` — `3840P` resolution marker not in `QUALITY_PATTERNS`.
- `beIN Sp⚽rts MAX 4`, `… AFC SS` — genuinely **different channels** (beIN Sports MAX / AFC).

These (esp. the `3840P` resolution marker and bare region prefixes like `SA`) are candidates for a separate
normalization pass.

## Testing
Exact `normalize_name` outputs (case-preserving):

| Input | Output (`normalize_name`, case-preserving) |
|---|---|
| `SP⚽RTS` | `SPoRTS` (mid-word ball → lowercase `o`; → `sports` after processing) |
| `Sp⚽rts` | `Sports` |
| `BE⚽` (edge ball) | `BE` (ball stripped, not mapped) |
| `UEFA CHAMPIONS LEAGUE ⚽` (standalone) | `UEFA CHAMPIONS LEAGUE` |
| `♬♬♬ MUSIC TV ♬♬♬` | `MUSIC` (notes stripped; existing ` TV` suffix rule also drops `TV`) |
| `️HULU` (leading VS16) | `HULU` |
| `Gold` (collision guard) | `Gold` |
| `Россия` (non-Latin guard) | `Россия` |

- **End-to-end:** `find_best_match("beIN Sports", ["### beIN SP⚽RTS ###"])` returns a match at threshold 95
  (was no-match).
- **No-regression:** existing 155 tests stay green; ASCII names byte-identical.

## Success criteria
1. `SP⚽RTS`/`Sp⚽rts` normalize to the word `sports`; the base `beIN Sports` feed matches (0 → match).
2. Decoration pictographs (`♬`,`☾`, standalone `⚽`) and zero-width selectors are removed.
3. Corpus diff: only emoji-bearing names change; channel DB unchanged; full suite green.
