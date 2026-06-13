# Design: Phase-1 US Alias Matching + Taxonomy Reconciliation

**Date:** 2026-06-13
**Status:** Approved (design, rev 2 post-QA); pending implementation plan
**Branch:** `feature/alias-matching`

> **Rev 2** rewrites the integration after an adversarial QA review (see
> "QA findings addressed" at the end). The original "copy Lineuparr's
> `alias_match` near-verbatim and run it as `fuzzy_match` Stage 0" approach was
> rejected: the two matchers have drifted too far to copy, and a Stage-0 result
> is silently discarded by a downstream re-match loop. This revision uses an
> **SM-native, exact-normalized alias layer** integrated at the call site.

## Problem

Stream-Mapparr matches a canonical channel name against messy IPTV stream names
using fuzzy similarity. Fuzzy matching is structurally blind to:
- **Abbreviations** with near-zero similarity (`TWC`→Weather Channel, `FS1`→Fox Sports 1).
- **Rebrands** (`EPIX`↔`MGM+`, `WGN America`↔`NewsNation`).

### Evidence (coverage analysis, 2026-06-13)
Stream-Mapparr's real `FuzzyMatcher` (threshold 80) over **11,795 real US IPTV
streams** against 297 alias-keyed channels: of 196 channels with a stream present,
fuzzy reaches 162; **exact-normalized alias matching reaches 34 more → ~17% lift**.
Crucially, that measurement used **exact-normalized alias matching only** — the
same mechanism this revision implements — so the measured lift maps directly onto
this design.

## Scope

Two sequenced deliverables. Deliverable 1 (docs) first.

### Out of scope (Phase 2)
- UK / FR / CA alias tables and *active* country-scoped overrides.
- **Fuzzy-alias matching** (high-similarity, not exact). This is where Lineuparr's
  `_has_token_overlap` / `_length_scaled_threshold` / `_is_distinctive` helpers and
  threshold retuning would be needed; deferred until the exact layer is proven.
- The shared-core `fuzzy_matcher.py` refactor across the 4 plugins.

---

## Deliverable 1 — Taxonomy reconciliation (docs only)

`Stream-Mapparr/US_channels.json` is a ~31,621-entry auto-generated file using
`type` values `National`/`Regional`/`International`, not the documented
`premium/cable/national`. But the matcher and `scripts/validate_databases.py`
(`BROADCAST_MARKER = "broadcast"`, lines 14/35) only treat a `type` **containing
`"broadcast"`** as special (OTA, needs `callsign`); everything else is
name-matched. **The drift is docs-only — the validator already encodes the real
contract.**

### Changes
1. Update `CLAUDE.md` (project copy) and the workspace-root `CLAUDE.md` reference
   to state the real contract: *only a `type` containing `"broadcast"` is
   semantically significant (OTA, needs `callsign`); all other `type` strings are
   free-form and name-matched.* Fix the "Adding Channel Databases" example.
2. **PR #30 (Norwegian channels) is valid as-is.** Update the review comment to
   retract the `type`-normalization request; keep the "add NO to docs" note.

### Non-goals
No rewrite of the 31k-row DB; no matcher/validator code change.

---

## Deliverable 2 — US alias matching (SM-native, exact-normalized)

### Why exact-normalized only
1. It produced the **entire measured 17% lift** (the analysis used exact-normalized
   matching).
2. It reuses SM's existing `normalize_name` and needs **none** of Lineuparr's
   drifted helpers (`_has_token_overlap`, `_is_distinctive`, `_is_group_header`,
   `_length_scaled_threshold`) and **no threshold retuning** against SM's bug-026
   `calculate_similarity` formula.
3. It is **inherently safe against the numeric-sibling bug (bug-021):** an
   exact-normalized alias for `FS1` can only resolve to streams normalizing to its
   curated variants (`FS1`, `Fox Sports 1`), never `FS2`. No fuzzy-alias = no
   numeric false-positive surface.

### Architecture

The blocker the QA review found: after `fuzzy_match` returns one best stream, both
call sites (`plugin.py:2287-2354` and `:2533-2546`) **rebuild the result set by
re-filtering every stream against the *channel name*** (exact / length-ratio
substring / token overlap). An alias-only stream (low name similarity *by
definition*) fails that filter and is dropped. Therefore the alias layer must
**force-include alias hits into `matching_streams`, independently of the fuzzy
result and the re-match loop.**

```
per channel group:
  OTA callsign match               (unchanged, first — alias never overrides it)
    │ miss
    ▼
  alias_hits = matcher.alias_lookup(channel_name, stream_names, alias_map)   ← NEW
  matched   = matcher.fuzzy_match(channel_name, stream_names)                (unchanged)
  matching_streams =
       (streams whose name ∈ alias_hits)              ← force-included, bypasses re-filter
     ∪ (existing re-match-loop result when `matched`) (unchanged behavior)
  → existing _sort_streams_by_quality + _deduplicate_streams
```

If `matched` is None but `alias_hits` is non-empty, the channel still gets its
alias streams (the alias-only case the old design lost).

### Components

**`Stream-Mapparr/fuzzy_matcher.py`**
- `alias_lookup(self, query_name, candidate_names, alias_map, user_ignored_tags=None, ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True)`
  → returns the list of `candidate_names` whose normalized form (spaced **or**
  `&-/space`-stripped) exactly equals the normalized form of any alias variant of
  `query_name`. Pure; uses only `normalize_name`. ~30 lines. No new helpers, no
  similarity calls, no threshold logic.

**`Stream-Mapparr/aliases.py` (new)**
- `CHANNEL_ALIASES` — US alias table keyed on **canonical channel names** (the
  `query_name` is the user's Dispatcharr channel name, so keys are common channel
  names, *not* rows of the 31k DB). Seeded from Lineuparr's US keys + the verified
  lift set. Carries Lineuparr's guard comments.
- `COUNTRY_ALIASES` — scaffold dict, US-empty in Phase 1.

**`Stream-Mapparr/plugin.py`**
- `_build_alias_map(self, settings, country)` — merges `CHANNEL_ALIASES` +
  `COUNTRY_ALIASES[country]` + parsed `custom_aliases`. JSON parsed inline with
  `try/except` (no `_clean_json_text` port); bare string accepted as a single
  alias; malformed JSON logged and ignored; alias values normalizing to empty are
  dropped.
- Build the alias map once where the matcher is built (~line 1239); store on `self`.
- Both call sites (**2270** and **2517** — confirmed the only two `fuzzy_match`
  calls) gain the `alias_lookup` force-include step.
- New `custom_aliases` settings field.

### Settings

| ID | Type | Default | Description |
|---|---|---|---|
| `custom_aliases` | `string` | "" | JSON object mapping a channel name to extra alias names (bare string accepted as one alias). Blank = built-in only. *(SM uses `string` fields; JSON-in-single-line is the known constraint — a multi-line type is a Phase-2 nicety if Dispatcharr's renderer supports it.)* |

### Guards
- **No bare short-token alias values** (`UP`/`GET`/`GAC`) — they normalize to 2–3
  chars and would exact-match unrelated short streams.
- **No regional catch-all** (FanDuel-regional lesson).
- **Country-scoping scaffold** (`COUNTRY_ALIASES`, bug-063) — mechanism present,
  US-empty.
- **Callsign precedence** — alias layer runs after OTA callsign matching.
- **Numeric-sibling (bug-021):** not applicable to exact-normalized matching (see
  "Why exact-normalized only").

### Error handling
- Malformed `custom_aliases` → warn, use built-in only; never raise into the loop.
- Empty/None `alias_map` → `alias_lookup` returns `[]`; pipeline behaves as today.

## Testing

`tests/`, Django-stubbed via `conftest.py`; `alias_lookup` is pure (no new stubs).

**Critical regression (locks the QA blocker):**
- An alias-only stream that FAILS the channel-name re-filter is still force-included.
  e.g. channel `FS1`, streams `["Fox Sports 1"]`, fuzzy returns None →
  `alias_lookup` returns `["Fox Sports 1"]` → it appears in the final result.

**Other SM-specific:**
- Exact-normalized recoveries from the verified lift set (`FS1`←Fox Sports 1,
  `TCM`←Turner Classic Movies, `WGN America`←NewsNation, `LMN`←Lifetime Movie
  Network, `SundanceTV`←Sundance, …).
- Numeric-sibling safety: channel `FS1`, streams `["FS2","Fox Sports 2"]` →
  `alias_lookup` returns `[]`.
- Bare-token rejection: an alias value `"up"` does not match arbitrary short streams.
- `custom_aliases` parsing: valid object, bare-string value, malformed JSON
  (ignored + warning), non-object (ignored).
- `_build_alias_map` merge precedence: custom alias wins over built-in.
- Callsign precedence: an OTA channel still resolves via callsign, not alias.

**Validation:** `python -m pytest -q` green; CI green.

## Versioning / deploy
Bump via `bump_version.py` (syncs `plugin.json`+`plugin.py`; `plugin.json` mtime
drives hot-reload). Porting to sibling plugins is out of scope.

## Success criteria
1. The verified exact-normalized lift channels that currently get no match resolve
   to their correct stream in the test fixtures, **including** alias-only channels
   that fail the name re-filter (the blocker case).
2. No regression in existing matches (full suite green; numeric-sibling and
   callsign locks intact).
3. `custom_aliases` lets a user add a mapping that resolves a missed stream without
   code changes.
4. Docs describe the real `type` contract; PR #30 review updated.

---

## QA findings addressed (rev 1 → rev 2)
- **BLOCKER (re-match loop drops alias hits):** integration moved to a call-site
  force-include, evaluated independently of the fuzzy result. Locked by a dedicated
  regression test.
- **BLOCKER (helper web absent) + BLOCKER (`calculate_similarity` drift/bug-026):**
  eliminated by using exact-normalized matching only — no Lineuparr helpers, no
  similarity calls, no threshold retune. Fuzzy-alias deferred to Phase 2.
- **MAJOR (call sites = 2 not 3):** corrected to {2270, 2517}; 4215 removed.
- **MAJOR (key reconciliation underscoped):** clarified that keys are canonical
  channel names (= the query), seeded from Lineuparr's US keys + the verified lift
  set — not a 1:1 reconciliation against the 31k auto-generated DB rows.
- **MINOR (`_clean_json_text` absent):** replaced with inline `try/except`.
- **MINOR (settings type):** documented the `string`/JSON constraint explicitly.
