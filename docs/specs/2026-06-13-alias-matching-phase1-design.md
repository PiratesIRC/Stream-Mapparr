# Design: Phase-1 US Alias Matching + Taxonomy Reconciliation

**Date:** 2026-06-13
**Status:** Approved (design); pending implementation plan
**Branch:** `feature/alias-matching`

## Problem

Stream-Mapparr matches a canonical channel name (`channel_name` from the channel
databases / Dispatcharr channels) against messy IPTV stream names using fuzzy
similarity. Fuzzy matching is structurally blind to two large classes of real
streams:

- **Abbreviations** with near-zero string similarity to the canonical name
  (`TWC`→Weather Channel, `BTN`→Big Ten Network, `FS1`→Fox Sports 1).
- **Rebrands** (`EPIX`↔`MGM+`, `WGN America`↔`NewsNation`,
  `getTV`↔`Great Entertainment Television`).

Lineuparr (a sibling Dispatcharr plugin sharing a common `fuzzy_matcher.py`
ancestor) solved this with a curated **alias table** + an `alias_match` pass that
runs before fuzzy. Both plugins match in the **same direction** (canonical name →
stream-name variants), so the mechanism ports directly.

### Evidence (coverage analysis, 2026-06-13)

Ran Stream-Mapparr's real `FuzzyMatcher` (threshold 80 = "normal") over **11,795
real US IPTV streams** against the **297 alias-keyed channels** Lineuparr's table
targets:

| Bucket | Count | % |
|---|---|---|
| Both fuzzy + alias find a stream | 156 | 53% |
| **Alias-only (fuzzy NO-MATCH lift)** | **34** | 11% |
| Fuzzy-only (alias adds nothing) | 6 | 2% |
| Neither (stream absent from dump) | 101 | 34% |

Of the 196 channels where a correct stream exists, current fuzzy reaches 162;
aliases are the **only** way to reach the other 34 → **17% recovery lift** (a
floor — exact-normalized only; real `alias_match` also does fuzzy-alias).

The analysis also confirmed two design constraints empirically:
- **Aliases preempt fuzzy false positives** (e.g. `CNN En Español` →
  "CSI EN ESPAÑOL" @85 is a wrong fuzzy match a high-confidence alias pass avoids).
- **Cross-market collisions are real** (`CTV Nature Channel`←`Discovery Science`
  is a CA-only rebrand alias that false-matches US "Discovery Science" — bug-063).
  Country-scoping is mandatory, not optional.

## Scope

Two sequenced deliverables. Deliverable 1 is docs-only and goes first because it
defines the channel-name contract the alias keys are reconciled against.

### Out of scope (Phase 2)
- UK / FR / CA alias tables and *active* country-scoped overrides.
- Expanding fuzzy-alias coverage beyond the ported behavior.
- The shared-core `fuzzy_matcher.py` refactor across the 4 plugins (DEV-WORKFLOW §7.1).

---

## Deliverable 1 — Taxonomy reconciliation (docs only)

### Finding
The live `Stream-Mapparr/US_channels.json` is a ~31,621-entry auto-generated file
using `type` values `National` / `Regional` / `International` — not the
`premium/cable/national` taxonomy documented in `CLAUDE.md`. However, the matcher
and validator only treat a `type` **containing the substring `"broadcast"`** as
special (OTA, requires a `callsign`); every other `type` string falls through to
name-based matching.

`scripts/validate_databases.py` already implements exactly this contract
(`BROADCAST_MARKER = "broadcast"`; broadcast → needs callsign; else → needs
`channel_name`). **The drift is purely in the documentation.**

### Changes
1. Update `CLAUDE.md` (project copy) and the workspace root `CLAUDE.md` reference
   to state the real contract: *only a `type` containing `"broadcast"` is
   semantically significant (OTA, needs `callsign`); all other `type` strings are
   free-form and name-matched.* Adjust the "Adding Channel Databases" example
   accordingly (keep `broadcast (OTA)` and `premium/cable/national` as the
   recommended labels, but document that other strings are accepted).
2. **PR #30 (Norwegian channels) becomes valid as-is.** Update the review comment
   to retract the `type`-normalization request; keep only the "add NO to the
   channel-database docs" note.

### Non-goals
- No rewrite of the 31k-row US database.
- No matcher or validator code change.

---

## Deliverable 2 — US alias matching

### Architecture

**Integration mechanism (Option A — approved):** thread an optional `alias_map`
parameter into `FuzzyMatcher.fuzzy_match()` and run `alias_match` as its Stage 0,
before the existing exact → substring → fuzzy stages. This reproduces Lineuparr's
alias-first ordering, is backward-compatible (omitting `alias_map` = today's
behavior), and touches the ~3 existing call sites only by passing one argument —
the smallest footprint in SM's large `plugin.py`.

The alias pass runs **after** the existing OTA callsign matching path, so it never
overrides a callsign hit.

```
per channel group:
  OTA callsign match  (unchanged, first)
    │ miss
    ▼
  fuzzy_match(channel, streams, alias_map=…)
    Stage 0: alias_match   ← NEW (skipped when alias_map is None/empty)
    Stage 1: exact
    Stage 2: substring
    Stage 3: token-sort fuzzy
```

### Components

**`Stream-Mapparr/fuzzy_matcher.py`**
- `alias_match(self, query_name, candidate_names, alias_map, user_ignored_tags=None)`
  — copied near-verbatim from Lineuparr. Returns `(stream_name, score, "alias")`
  for exact-normalized alias hits (score 100) and high-similarity alias hits
  (length-scaled threshold), best first.
- `_length_scaled_threshold(self, base_threshold, match_len)` — copied from
  Lineuparr; raises the effective threshold for short strings so a short alias
  can't fuzzily 100-match an unrelated short stream.
- `fuzzy_match(...)` gains optional `alias_map=None`; when provided and non-empty,
  runs `alias_match` first and returns its top result if any.
- **SM-specific guard:** the numeric-sibling guard (bug-021) is applied to
  `alias_match`'s fuzzy branch so an alias can never reintroduce FS1/FS2 /
  ESPN2/ESPN+ confusion. (Exact-alias is already safe by construction.)

**`Stream-Mapparr/aliases.py` (new)**
- `CHANNEL_ALIASES` — the **reconciled US subset** of Lineuparr's table: US-relevant
  keys, with each key reconciled to the name as it appears in SM's `US_channels.json`
  (the 34 verified lift cases plus the rest of the US head). Carries over Lineuparr's
  inline guard comments (bare-token prohibition, FanDuel regional catch-all warning).
- `COUNTRY_ALIASES` — scaffold dict, present for the country-scoping mechanism but
  US-empty in Phase 1.

**`Stream-Mapparr/plugin.py`**
- `_build_alias_map(self, settings, country)` — copied from Lineuparr: merges
  `CHANNEL_ALIASES` + `COUNTRY_ALIASES[country]` + parsed `custom_aliases`
  (user JSON, last wins; bare string accepted as a single alias; malformed JSON
  logged and ignored).
- Build the alias map once where the matcher is constructed (~line 1239) and pass
  it into the `fuzzy_match` call sites (plugin.py ~2270, ~2517, ~4215).
- New settings field `custom_aliases` (string / JSON textarea) with help text
  mirroring Lineuparr's.

### Settings

| ID | Type | Default | Description |
|---|---|---|---|
| `custom_aliases` | string | "" | JSON object mapping a channel name to extra alias names (a bare string is accepted as a single alias). Blank = built-in aliases only. |

### Guards (carried from Lineuparr's hard-won lessons)
- **No bare short-token values** (`UP`/`GET`/`GAC`) — they normalize to 2–3 chars
  and 100-match anything.
- **No regional catch-all** (the FanDuel-regional note) — a generic parent EPG on
  regional feeds is worse than no match.
- **Country-scoping** (`COUNTRY_ALIASES`, bug-063) — mechanism present even though
  US-empty in Phase 1.
- **Numeric-sibling guard** (bug-021) — applied to the fuzzy-alias branch (SM-specific).
- **Callsign precedence** — alias pass runs after OTA callsign matching.

### Error handling
- `custom_aliases` malformed JSON → log a warning, fall back to built-in aliases
  (never raise into the match loop).
- Empty / `None` `alias_map` → `fuzzy_match` behaves exactly as today (Stage 0 no-op).
- Alias values that normalize to empty are dropped at map-build time.

## Testing

Mirrors the project's regression-lock convention (`tests/`, Django-stubbed via
`conftest.py`, no Django at test time).

**Ported from Lineuparr:**
- Callsign guard: an alias must not override an OTA callsign match.
- Country-alias isolation (bug-063): a country-scoped alias must not leak across markets.
- Bare-token rejection: short bare tokens are not honored as alias values.

**SM-specific:**
- The **34 verified lift cases** become regression locks (exact-normalized alias
  recovers a stream fuzzy misses): `FS1`←Fox Sports 1, `TCM`←Turner Classic Movies,
  `WGN America`←NewsNation, `MS Now`←MSNBC, `EPIX 1`←EPIX, `LMN`←Lifetime Movie
  Network, etc.
- Numeric-sibling not undone by alias: with FS1/FS2 streams present, an FS1 alias
  match never returns an FS2 stream.
- `custom_aliases` parsing: valid JSON object, bare-string value, malformed JSON
  (ignored with warning), non-object (ignored).
- `_length_scaled_threshold` raises the bar for short strings.

**Validation:** `python -m pytest -q` green; CI (`.github/workflows/ci.yml`) green.

## Versioning / deploy

- Bump version via `Stream-Mapparr/bump_version.py` (keeps `plugin.json` +
  `plugin.py` in sync; `plugin.json` mtime bump is what triggers Dispatcharr
  hot-reload).
- Port the matcher addition to the sibling plugins' `fuzzy_matcher.py` copies is
  **not** part of Phase 1 (tracked under the shared-core refactor).

## Success criteria

1. With the built-in US alias table active, the 34 lift channels that currently
   get no match resolve to their correct stream in the test fixtures.
2. No regression in existing matches (full suite green, including the numeric-sibling
   and callsign locks).
3. `custom_aliases` lets a user add a mapping that resolves a stream the built-in
   table misses, without code changes.
4. Docs accurately describe the `type` contract; PR #30 review updated.
