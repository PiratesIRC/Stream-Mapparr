# Design: Unicode normalization of stylized stream markers (rev 2, post-QA)

**Date:** 2026-06-13
**Status:** Approved approach A; **rev 2 pivots to A′′ after QA** — pending user re-approval → plan
**Component:** `Stream-Mapparr/fuzzy_matcher.py` (`normalize_name`)

> **Why rev 2:** the QA review + verification found that the rev-1 plan ("NFKD-fold
> `ᴿᴬᵂ`→`RAW`, then strip the ASCII `RAW`/`VIP`/`GOLD` tokens") is unsafe: folding
> erases the superscript signal, and stripping ASCII `GOLD`/`VIP`/`RAW` collides with
> **real channel names** (UKTV **Gold**, **VIP** feeds). It also missed small-caps
> (`ꜰʜᴅ`, present 66× in the corpus — NFKD doesn't fold them) and an ordering bug
> (digit/letter spacer splits `60fps`→`60 fps`). Rev 2 fixes this by stripping
> **whole stylized-Unicode tokens** instead of fold-then-strip-ASCII.

## Problem

Streams carry stylized-Unicode format/tier markers: superscript `RK: WEATHERNATION ᴿᴬᵂ`,
`… ⁶⁰ᶠᵖˢ`, `… ⁴ᴷ`, `… ⱽᴵᴾ`, `… ³⁸⁴⁰ᴾ`, `… ⁽ᴮᴷ⁾`; small-caps `… ꜰʜᴅ`; and bullets `◉`.
`normalize_name` strips tags with **ASCII** regexes, so the stylized forms survive:
`normalize_name("RK: WEATHERNATION ᴿᴬᵂ") == "WEATHERNATION ᴿᴬᵂ"`, which can't match
channel `WeatherNation`. (An alias can't fix it — the candidate keeps `ᴿᴬᵂ`.)

## Goal

Remove stylized-Unicode decoration from names so they match, **without** harming real
channel names that happen to share a word with a tier marker (Gold, VIP).

## Approach A′′ (chosen): strip whole stylized tokens, then NFKD-canonicalize

Insert two steps at the **top** of `normalize_name`, before the existing ASCII tag regexes:

1. **Strip stylized-marker tokens.** Split on whitespace; drop any token whose characters
   are **all** in the "decorative" Unicode set and contains **no** ASCII alphanumeric.
   Decorative set = superscripts/subscripts (U+2070–U+209F, U+00B2/B3/B9), superscript
   **modifier letters** (U+1D2C–U+1D6B — this is what `ᴿᴬᵂ`/`ᴴᴰ`/`ⱽᴵᴾ`/`ᴮᴷ` are),
   **Latin small capitals** (the small-cap letters in U+1D00–U+1DBF + U+A730–A732, e.g.
   `ꜰʜᴅ`), and standalone geometric/bullet symbols actually seen (`◉` U+25C9). A token
   like `ᴿᴬᵂ`, `⁶⁰ᶠᵖˢ`, `³⁸⁴⁰ᴾ`, `⁽ᴮᴷ⁾`, `ꜰʜᴅ`, `◉` is all-decorative → removed. A token
   with any ASCII alnum (`WEATHERNATION`, `GOLD`, `VIP`, `(BK)`, `RK:`) is **kept**.
2. **NFKD-canonicalize the remainder** (`unicodedata.normalize('NFKD', …)`) — folds
   fullwidth, ligatures, accented bases, etc. for general robustness. (The decorative
   tokens are already gone, so no `GOLD`/`VIP` collision is introduced.)

Then the existing pipeline (ASCII quality/regional/geographic/misc stripping, space-norm)
runs unchanged.

### Why this over rev-1 fold-then-strip
- **No name collision:** only the *superscript/small-cap form* is removed. ASCII `Gold`,
  `VIP`, `Raw` channel names are untouched (they contain ASCII alnum → kept).
- **General:** targets character *classes* (decorative ranges), not a per-marker list, so
  new superscript markers and the small-cap class are covered automatically.
- **No ordering bug:** we don't add ASCII `fps`/resolution patterns, so the digit/letter
  spacer (F1) is irrelevant — the whole `⁶⁰ᶠᵖˢ`/`³⁸⁴⁰ᴾ` token is removed before it.
- **Bonus:** removes `◉` bullets and small-caps that rev 1 missed.

### Gating
The stylized-token strip is **input cleaning**, not tag filtering — a token written
entirely in superscript/small-caps is decoration regardless of `tag_handling`. It runs
unconditionally (like the existing space/punct normalization), NOT behind
`ignore_quality`. Rationale: keeping a `ᴿᴬᵂ` token never helps a match and there's no
"keep stylized superscripts" use case. (Confirmed at QA: callsign extraction uses the
raw name, not `normalize_name`, so this doesn't disturb it.)

## Scope

**In:** tokens composed entirely of the decorative Unicode classes above (superscripts,
superscript modifier letters, Latin small-caps, the `◉` bullet) + NFKD canonicalization
of the remainder.

**Out (explicit):**
- **Mixed tokens** with emoji-letter substitution: `SP⚽RTS` / `Sp⚽rts` (578×) — the token
  has ASCII alnum, so it's kept; `⚽` is not folded. Separate effort.
- Non-Latin scripts (Cyrillic/Arabic/CJK channel names) are **kept** — they are NOT in the
  decorative set, so the all-decorative rule never strips them. (Critical safety property.)

## What does NOT change
Match pipeline order, exact/alias/fuzzy stages, thresholds, the numeric-sibling guard
logic, and the ASCII tag regexes. This is purely input normalization. `process_string_for_matching`
is **not** changed (it receives `normalize_name` output, so its NFD is a harmless no-op
on already-clean input — rev 1's NFD→NFKD there was dead and is dropped).

## Validation (risk control)

- **Corpus diff (one-off script):** run `normalize_name` old-vs-new over the full real
  stream pool (`Lineuparr/_stream_exports/streamq_streams.csv`, ~54k names — confirmed to
  exist) and all 12 `*_channels.json` names. Gate: every change is a desirable
  decoration-removal; **no real channel name altered**. Named watch-items from QA:
  `№`→`No`, `™`→`TM`, `µ`, fractions — confirm none appear in a harmful way (these come
  from the NFKD step, step 2).
- **Non-Latin safety check:** assert the diff does not blank or truncate any non-ASCII
  *letter* token (Cyrillic/Arabic/CJK) — the decorative predicate must exclude general
  non-ASCII letters.

## Testing
- **Recovery:** `normalize_name("RK: WEATHERNATION ᴿᴬᵂ")` → `weathernation`; `… ⁶⁰ᶠᵖˢ`,
  `… ⁴ᴷ`, `… ⱽᴵᴾ`, `… ꜰʜᴅ`, `◉ Foo` all lose the marker.
- **Collision guard (the key new test):** `normalize_name("Gold")` == `"gold"`,
  `normalize_name("VIP")`/`"Raw"` unchanged — ASCII tier-words are NOT stripped.
- **Non-Latin guard:** an all-Cyrillic token survives normalization.
- **No-regression:** sample of plain ASCII names normalize byte-identically to today;
  existing 139 tests stay green.
- **Numeric-sibling (F3):** a name with a folded superscript digit (e.g. `Foo ²`) does not
  spuriously gain a digit token that breaks the FS1/FS2 guard (the all-decorative `²`
  token is stripped in step 1, so no digit leaks to the guard).

## Success criteria
1. `WeatherNation` matches `RK: WEATHERNATION ᴿᴬᵂ`; superscript + small-cap + bullet
   decorations are removed.
2. ASCII channel names sharing a tier word (`Gold`, `VIP`) are unaffected; non-Latin names
   preserved.
3. Corpus diff: only decoration removals + benign NFKD folds; full suite green.
