# Design: Numeric resolution-marker normalization

**Date:** 2026-06-13
**Status:** Approved (recon-validated; user chose general NNN[pi] scope, leave bare numbers). Ready for plan.
**Component:** `Stream-Mapparr/fuzzy_matcher.py` (`normalize_name`)
**Related:** third in the matcher-normalization series (stylized-Unicode → emoji-as-letter → this).

## Problem

Some streams carry a numeric resolution tag the quality patterns don't recognize:
`### beIN SP⚽RTS 4K 3840P ###`, `## TR| BEIN SPORTS GOLD ᵁᴴᴰ 3840P ##`. `QUALITY_PATTERNS`
strips keyword tags (`4K/8K/UHD/FHD/HD/SD/…`) but not `3840P` / `2160P` / `1080P` / `720P`, so the
leftover `3840`/`P` tokens pollute the normalized name and drop fuzzy similarity below threshold
(e.g. the beIN Sports `4K 3840P` feed stays unmatched even after the emoji fix).

## Recon (real corpus, 53,992 names)

`NNN[pi]` markers appear in **138 names**:

| token | count | note |
|---|---|---|
| `720P` | 94 | mostly **parenthesized** `(720P)` — already removed by the existing `MISC_PATTERNS` paren-strip |
| `3840P` | 42 | **not** parenthesized — the real gap (4K width tag) |
| `1080P` | 1 | |
| `380P` | 1 | non-standard but clearly a resolution |

- **Channel-DB collision check: 0** — no `*_channels.json` `channel_name` contains a resolution-like token, so stripping `\d{3,4}[pi]` is safe.
- **Bare standalone numbers** (`480`/`576`/`720`) occur in only **3 names** (one odd `ESPN+ PPV 480/576/720` set) — **out of scope** (stripping a bare 3-4-digit number has no `p/i` anchor and is riskier).

## Approach (chosen): a `RESOLUTION_PATTERNS` list in the `ignore_quality` stage

```python
# Numeric resolution markers the keyword QUALITY_PATTERNS miss: 720p, 1080p/i, 2160p, 3840P, 480p, ...
# Any 3-4 digit run followed by p/i (optional space). Applied with re.IGNORECASE like QUALITY_PATTERNS.
RESOLUTION_PATTERNS = [
    r'\b\d{3,4}\s*[pi]\b',
]
```

Applied inside the existing `if ignore_quality:` block, immediately **before** the `QUALITY_PATTERNS`
loop (and well before the digit/letter spacer, so the glued `3840P` is matched before it would be split to
`3840 P`):

```python
        if ignore_quality:
            for pattern in RESOLUTION_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
            for pattern in QUALITY_PATTERNS:
                name = re.sub(pattern, '', name, flags=re.IGNORECASE)
```

> **Ordering (important):** resolution stripping must run **before** `QUALITY_PATTERNS`. The middle
> standalone-quality pattern `\s+\b(4K|…)\b\s+` consumes **both** spaces around `4K`, so a name like
> `SPoRTS 4K 3840P` would collapse to `SPoRTS3840P` — gluing the word to the marker and destroying the
> `\b` boundary the resolution regex needs. Stripping `3840P` first (while the spaces still exist) avoids
> this. (This matches the validation prototype, which removed the marker from the raw input.)

- **Gated by `ignore_quality`** — a resolution marker is a quality tag, so it follows the same flag as
  `4K`/`HD` (unlike the unconditional emoji/Unicode input-cleaning).
- The `\s*` tolerates an already-spaced `3840 P`; the `\b…\b` anchors prevent matching inside a longer
  number (`38400P` won't match) and the `p/i` anchor keeps bare numbers (`1080`, `Studio 1080`) untouched.

## What does NOT change
Pattern order otherwise, thresholds, the numeric-sibling guard, `_strip_stylized_tokens`,
`_normalize_emoji`, and `process_string_for_matching`. Single-digit channel numbers (`Channel 4`) and
bare numbers (`Studio 1080`) are unaffected (no `p/i`).

## Validation (recon, GO)

Old-vs-new `normalize_name` (faithful prototype: strip the resolution pattern from the input — ASCII, so
equivalent to the in-quality-stage strip) over the corpus + channel DBs:

| Set | Compared | Changed | Harmful |
|---|---|---|---|
| `streamq_names.txt` | 53,992 | 43 | **0** (every changed name had an `NNN[pi]` token; the parenthesized `720P` were already handled) |
| All `*_channels.json` `channel_name` | 42,897 | **0** | 0 |

- **beIN `4K 3840P` variant:** `find_best_match("beIN Sports", ["### beIN SP⚽RTS 4K 3840P ###"])`
  (with the deployed emoji fix) goes **no-match → 100** once `3840P` is stripped.
- Sample diffs: `BEIN SPORTS GOLD ᵁᴴᴰ 3840P`→`BEIN SPORTS GOLD`, `RELAX 1 ᵁᴴᴰ 3840P`→`RELAX 1`.

## Scope — out (explicit)
- **Bare standalone numbers** (`480`/`576`/`720`, 3 names) — riskier, deferred.
- Non-emoji/non-resolution beIN noise from earlier specs: `SA …` region prefix, and `beIN Sports MAX`/`AFC`
  (genuinely different channels) — still separate efforts.

## Testing
Exact `normalize_name` outputs (case-preserving):

| Input | Output |
|---|---|
| `BEIN SPORTS GOLD 3840P` | `BEIN SPORTS GOLD` |
| `RELAX 1 3840P` | `RELAX 1` |
| `Sky Sports 720P` | `Sky Sports` |
| `Foo 1080i` | `Foo` |
| `Foo 2160p` | `Foo` |
| `Channel 4` (collision guard) | `Channel 4` |
| `Studio 1080` (bare number, no p/i) | `Studio 1080` |
| `CNN HD` / `ITV1` (no-regression) | `CNN` / `ITV 1` |

- **End-to-end:** `find_best_match("beIN Sports", ["### beIN SP⚽RTS 4K 3840P ###"])` returns a match at
  threshold 95 (was no-match).
- **No-regression:** existing 167 tests stay green.

## Success criteria
1. `3840P`/`2160P`/`1080P`/`720P`/etc. resolution markers are stripped (gated by `ignore_quality`).
2. The beIN Sports `4K 3840P` feed matches (0 → match), on top of the emoji fix.
3. Bare numbers and single-digit channel numbers are untouched; channel DB unchanged; full suite green.
