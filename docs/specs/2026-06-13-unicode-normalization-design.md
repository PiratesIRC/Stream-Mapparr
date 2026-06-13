# Design: Unicode normalization of stylized stream markers (rev 3, post-recon)

**Date:** 2026-06-13
**Status:** **Approved** (rev 2 approach + two rev-3 refinements confirmed by the user). Ready for plan.
**Component:** `Stream-Mapparr/fuzzy_matcher.py` (`normalize_name`)

> **Revision history**
> - **rev 1** — "NFKD-fold `ᴿᴬᵂ`→`RAW`, then strip ASCII `RAW`/`VIP`/`GOLD`". Rejected at QA: folding then
>   stripping ASCII tier-words collides with real channel names (UKTV **Gold**, **VIP** feeds); also missed
>   Latin small-caps (NFKD doesn't fold them) and had a digit/letter spacer ordering bug.
> - **rev 2** — strip **whole stylized-Unicode tokens** (all chars decorative, no ASCII alnum), then NFKD the rest.
>   Approach approved by the user.
> - **rev 3 (this doc)** — two refinements surfaced by an empirical recon over the real 54k-name corpus and
>   confirmed by the user:
>   1. **Detect decoration by Unicode character _name_, not hard-coded code-point ranges.** The recon proved
>      rev 2's literal ranges miss real markers: `ʜ` (U+029C, IPA Extensions), `ⱽ` (U+2C7D, Latin Ext-C), and
>      `ˢ` (U+02E2, Spacing Modifier Letters) fall **outside** the listed ranges, so `ꜰʜᴅ`, `ⱽᴵᴾ`, and `⁶⁰ᶠᵖˢ`
>      would have survived. A name-based predicate catches all of them.
>   2. **Punctuation-aware token rule.** rev 2's "every char decorative" rule misses stylized markers glued to
>      ASCII punctuation — `◉:` (96×), `ᴴᴰ/ᴿᴬᵂ` (30×). The rule is relaxed to also strip a token whose remaining
>      chars are ASCII punctuation, while preserving every safety property.

## Problem

Streams carry stylized-Unicode format/tier markers: superscript `RK: WEATHERNATION ᴿᴬᵂ`,
`… ⁶⁰ᶠᵖˢ`, `… ⁴ᴷ`, `… ⱽᴵᴾ`, `… ³⁸⁴⁰ᴾ`, `… ⁽ᴮᴷ⁾`; small-caps `… ꜰʜᴅ`; and bullets `◉`.
`normalize_name` strips tags with **ASCII** regexes, so the stylized forms survive:
`normalize_name("RK: WEATHERNATION ᴿᴬᵂ")` keeps `ᴿᴬᵂ`, which can't match channel `WeatherNation`.
(An alias can't fix it — the candidate keeps `ᴿᴬᵂ`.) The recon found these markers on **20,537**
real stream names (the top one, superscript `ᴿᴬᵂ`, appears **11,931** times).

## Goal

Remove stylized-Unicode decoration from names so they match, **without** harming real channel
names that share a word with a tier marker (Gold, VIP) and **without** touching non-Latin scripts.

## Approach (chosen): strip stylized tokens by Unicode name, then NFKD-canonicalize

Insert one preprocessing step at the **top** of `normalize_name`, before the existing ASCII tag
regexes (i.e. before the `if ignore_quality:` block). It delegates to a module-level helper:

1. **ASCII fast path.** If the whole name is ASCII, return it unchanged — ASCII is invariant under
   both steps, so the common case stays allocation-cheap and calls no `unicodedata` machinery.
2. **Strip stylized-marker tokens.** Split on whitespace; drop any token that
   - contains **≥1 decorative char**, **and**
   - contains **no ASCII alphanumeric**, **and**
   - every char is **decorative OR ASCII punctuation/symbol**.

   "Decorative" is decided by `_is_decorative_char(ch)`: non-ASCII **and** either a curated ornament
   (`◉` U+25C9) or its `unicodedata.name(ch)` contains `SUPERSCRIPT`, `SUBSCRIPT`, `SMALL CAPITAL`,
   or `MODIFIER LETTER`. This name-based test covers superscripts, superscript "modifier-letter"
   capitals (`ᴿᴬᵂ`, `ⱽᴵᴾ`, `ᴮᴷ`, `ᴾ`), and Latin small-caps (`ꜰʜᴅ`) regardless of which Unicode
   block they live in.
3. **NFKD-canonicalize the remainder** (`unicodedata.normalize('NFKD', …)`) — folds fullwidth,
   ligatures, etc. for general robustness. (Decorative tokens are already gone, so no `GOLD`/`VIP`
   collision is introduced.)

Then the existing pipeline (ASCII quality/regional/geographic/misc stripping, space-norm) runs unchanged.

### Why the rule is safe (verified empirically)
- **No name collision:** a token must contain a decorative char to be dropped, so ASCII `Gold`, `VIP`,
  `Raw` (no decorative chars) are always kept.
- **Non-Latin safe:** Arabic/Cyrillic/CJK letters are non-ASCII **and** non-decorative, so a token
  containing them fails the "every char is decorative or ASCII-punct" test and is kept — even when a
  decorative char is glued to it (e.g. a hypothetical `العربيةᴴᴰ` stays whole).
- **Closes the punctuation blind spot:** `◉:` and `ᴴᴰ/ᴿᴬᵂ` are dropped because their non-decorative
  chars are all ASCII punctuation.
- **Lone punctuation untouched:** a token with no decorative char (`:`, `&`, `–`) is kept (no behavior change).

### Gating
The stylized-token strip is **input cleaning**, not tag filtering — a token written in
superscript/small-caps is decoration regardless of `tag_handling`. It runs **unconditionally** (like the
existing space/punct normalization), NOT behind `ignore_quality`. Keeping a `ᴿᴬᵂ` token never helps a
match and there is no "keep stylized superscripts" use case. (Callsign extraction uses the raw name, not
`normalize_name`, so this doesn't disturb it.)

## Scope

**In:**
- Tokens that are pure stylized decoration (decorative chars + optional ASCII punctuation, no ASCII alnum),
  including punctuation-glued ornaments (`◉:`, `ᴴᴰ/ᴿᴬᵂ`).
- NFKD canonicalization of the remaining text.

**Out (explicit, deferred):**
- **Emoji-letter substitution:** `SP⚽RTS` / `Sp⚽rts` (**681×** in the corpus) — the `⚽` (U+26BD) replaces
  the letter "o" inside an ASCII word, so the token has ASCII alnum and is correctly **kept**; neither step
  removes the ball. Fixing this needs a separate emoji→letter mapping pass (guess-prone) and is its own
  brainstorm. **Confirmed deferred by the user.**
- `™`/`®`/`©` substitutions are negligible (1/4/2 names) and left to NFKD's default behavior.

## What does NOT change
Match pipeline order, exact/alias/fuzzy stages, thresholds, the numeric-sibling guard logic, and the ASCII
tag regexes. This is purely input normalization. `process_string_for_matching` is **not** changed — it
receives `normalize_name` output and its NFD is a harmless no-op on already-canonical input.

## Validation (recon, GO)

A read-only old-vs-new diff (faithful prototype: prepend the preprocess to `normalize_name`'s input;
source untouched) over the real corpus and all channel databases:

| Set | Names compared | Changed | Harmful |
|---|---|---|---|
| `Lineuparr/_stream_exports/streamq_names.txt` | 53,992 | 18,846 | **0** |
| All `*_channels.json` `channel_name` values | 42,897 | 704 | **0** |

- **WeatherNation recovers:** `WEATHERNATION ᴿᴬᵂ` → `WEATHERNATION` (matches channel `WeatherNation`).
- Every changed name is a decoration removal or a benign NFKD fold (accented-Latin re-spelling that
  `process_string_for_matching` already folds downstream; symmetric on both stream and channel sides).
- Watch-items are non-issues in the real data: `№` U+2116 = **0** names, `™` = 1, `®` = 4, `©` = 2.
- Known residual after this change: ~96 `◉:`-style names are now cleaned; the only remaining stylized
  residue is the deferred `SP⚽RTS` emoji family (681).

## Testing
Exact `normalize_name` outputs (case-preserving; `normalize_name` does not lowercase):

| Input | Output |
|---|---|
| `WEATHERNATION ᴿᴬᵂ` | `WEATHERNATION` |
| `C-SPAN2 ᴴᴰ` | `C SPAN 2` |
| `ESPN ꜰʜᴅ` (small-caps) | `ESPN` |
| `◉: CNN` (punct-glued ornament) | `CNN` |
| `ENTERTAINMENT ᴴᴰ/ᴿᴬᵂ ⁶⁰ᶠᵖˢ` | `ENTERTAINMENT` |
| `Gold` (collision guard) | `Gold` |
| `VIP` (collision guard) | `VIP` |
| `Россия` (non-Latin guard) | `Россия` |
| `Fox Sports 1` / `Fox Sports 2` (no-regression) | unchanged |
| `CNN HD` / `ITV1` (no-regression) | `CNN` / `ITV 1` |

- **No-regression:** the existing 139 tests stay green; a sample of plain ASCII names normalize byte-identically.
- **Numeric-sibling guard:** `Fox Sports 1`/`2` normalize unchanged, so the FS1/FS2 guard is unaffected.

## Success criteria
1. `WeatherNation` matches `RK: WEATHERNATION ᴿᴬᵂ`; superscript + small-cap + bullet + punct-glued
   decorations are removed.
2. ASCII channel names sharing a tier word (`Gold`, `VIP`) are unaffected; non-Latin names preserved.
3. Corpus diff: only decoration removals + benign NFKD folds; full suite green.
