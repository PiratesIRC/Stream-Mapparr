# Stream-Mapparr CHANGELOG

## Unreleased

### Added
- **Regex pre-processing for stream names (issue #36).** Providers embed junk in stream
  names that defeats fuzzy matching — decorative glyphs, invisible padding, bouquet
  prefixes, badges — and each fix so far (box-bar strip, zero-width strip, mid-name tag
  spacing, Custom Ignore Tags) covers one known shape. **Stream Name Regex Rules** is a
  general escape hatch: a JSON list of `[find, replace]` regex pairs, applied in order to
  stream names before normalization and Custom Aliases, so a new provider quirk no longer
  needs a plugin release. It affects matching only — stream names in Dispatcharr are never
  modified, and quality sorting, zone routing, country restriction, and duplicate detection
  all continue to read the original name (a rule that strips a country prefix for matching
  purposes can't also blind country detection or collapse two genuinely distinct failover
  streams together). Group labels (Selected Groups / country restriction) are out of scope —
  rules only ever touch stream names.
  A new **🧪 Test Regex Rules** action previews the effect against every currently-loaded
  stream without changing anything: per-rule status, an N-of-M changed count, and up to 20
  before → after samples with invisible characters escaped so they're actually visible.
  **Validate Settings** also reports a one-line summary (`N ok, M rejected`), with full
  per-rule detail in the logs.
  Guardrails: patterns with a nested unbounded quantifier or alternation shape known to
  backtrack exponentially (e.g. `(a+)+`) are rejected before use; at runtime, names over 500
  characters skip pre-processing, a rule chain that grows a name past 4x its original length
  is reverted and stopped, and a regex pass is capped at 5 seconds cumulative — a bad rule
  degrades gracefully (and is reported) instead of freezing a worker.
  Thanks to the issue #36 reporter for the flagship example (an invisible zero-width space
  wrapped around a decorative block glyph, `UK ▎BBC 1 FHD`).

## v1.26.1971200 (July 16, 2026)

### Fixed
- **Dispatcharr still crashed (504 / UI hang) when zapping channels with a schedule
  configured — now fixed at the deeper layer (bug-136, follow-up to bug-127).** The
  bug-127 fix made scheduler re-arming a no-op, but its bookkeeping lived in module-level
  variables, which only survive plugin *re-instantiation*. On some installations
  Dispatcharr's plugin loader additionally **re-imports the plugin module** on nearly
  every streaming event (a reload loop in Dispatcharr itself: reacting to a stale
  `.reload_token` re-touches it, so with more than one worker the force-reload never
  converges — one plugin install/update can start it). Each re-import wiped the
  bookkeeping, so every channel zap silently started a NEW background scheduler thread
  and orphaned the old one — accumulating threads until the web workers starved and
  nginx returned 504, while streams kept playing. Scheduler state now lives outside the
  plugin module where the loader's reload cannot wipe it: re-arming after a module
  re-import finds the live thread and does nothing, stopping works across re-imports,
  and a genuine plugin update or schedule/settings change still restarts the thread.
  If you had a schedule configured and saw the UI die during channel surfing on
  1.26.1960020, this release is for you. (An upstream bug report about the reload loop
  itself is included in `docs/`.)
- **A typo in Scheduled Times (e.g. `0330 0445` — space instead of comma) no longer puts
  every request on the slow path.** An unparseable schedule now settles as "scheduler
  off" instead of being retried on every request.
- **Settings changes now reach an already-running schedule.** Changing e.g. the Channel
  Profile without touching the times used to leave the scheduled run firing with the old
  settings until restart; any settings change now restarts the scheduler thread with the
  fresh configuration.
- **Clearing the schedule while a scheduled run is in flight no longer taxes later
  requests.** The stop now releases the thread immediately (it exits on its own at its
  next check) and logs a distinct warning instead of a misleading "stopped".

## v1.26.1960020 (July 15, 2026)

### Fixed
- **East/West zone routing now covers lone unmarked channels, and understands Pacific
  (bug-132).** A channel like "Cinemax" (unmarked = national/East feed) with no East/West
  sibling was left un-routed, so the quality sort could put a West-coast stream first —
  viewers got the feed 3 hours behind. Zone ordering now also applies to any unmarked
  channel (East/national streams lead, West last), while lone *marked* channels are still
  left alone. A Pacific marker (`PACIFIC`, `(Pacific)`, `(PT)`) now counts as West, since a
  US premium channel's West feed is its Pacific-time feed.
- **Dispatcharr web UI 504 / crash when zapping through channels, with a schedule
  configured (bug-127).** Symptom: switching channels made the Stats page show
  "Unnamed Channel" then `Failed to retrieve channels by UUIDs: 504`, and the UI became
  unusable until a container restart. Cause: since the scheduler began arming from
  `__init__` (bug-065), and Dispatcharr re-instantiates the plugin on nearly every
  request, each channels API call was stopping and relaunching the background scheduler
  thread under a global lock — which, with a schedule set, serialized requests until
  nginx timed out. Scheduler arming is now idempotent: re-arming with an unchanged
  schedule does nothing, so ordinary channel activity no longer touches the scheduler.
  Only affected users who had a schedule configured; changing the schedule in the UI
  still takes effect immediately.
- **Streams with a quality tag in the MIDDLE of the name never matched (bug-126).** A stream
  like `UK ▎SKY NEWS FHD ◉ rec` failed to match the channel `Sky News`, while the `HD` and `SD`
  variants of the very same channel matched fine. Removing a quality tag also removed the spaces
  on both sides of it, gluing the surrounding words together: `SKY NEWS FHD rec` normalized to
  `SKY NEWSrec` (and `CNN [HD] USA` to `CNNUSA`). Tags at the end of a name were unaffected,
  which is why only the mid-name variants broke.
- **Custom Ignore Tags could not remove the leftover token.** Because ignore tags are applied
  after normalization and single-word tags match on word boundaries, `rec` could never match
  inside the glued `NEWSrec` — so adding it to the tag filter appeared to do nothing. With the
  glue fixed, a `rec` ignore tag now normalizes the stream to `SKY NEWS` and it matches at 100%.

Shared-matcher-core change, re-vendored to all four plugins. Note that a provider badge such as
`rec` is still a real leftover token: add it to **Custom Ignore Tags** to strip it (the `◉` glyph
itself is already removed automatically, so use `rec`, not `◉ rec`).

## v1.26.1931038 (July 12, 2026)

### Fixed
- **Web UI hang / `504 Gateway Time-out` after running Match & Assign (bug-117).**
  Long matching jobs could run **inline in the HTTP request**, which freezes an entire
  Dispatcharr uWSGI worker: uWSGI runs gevent with `gevent-early-monkey-patch`, so the
  matcher's CPU-bound loop never yields and blocks every request on that worker — not just
  its own. Symptom: the web UI dies and login stops responding, while live streams keep
  playing normally.

  The sync-vs-background gate was mis-calibrated in two ways. Its 25-second threshold was
  chosen for "the typical 30s gunicorn worker timeout", but Dispatcharr runs uWSGI behind
  nginx (`proxy_read_timeout` 300s). And the runtime estimate is optimistic — the
  0.8s-per-channel-group constant measured ~3.4x too low in the field (a 29-group job
  estimated at 23s actually took 1m18s), partly because the estimate is sized from the
  *previous* run's cached channel set, so changing the selected group/category is invisible
  to it.

  The decision is now `_should_run_sync()`, gated by `SYNC_THRESHOLD_SECONDS` (5s) and a new
  `ETA_SAFETY_FACTOR` (4.0), so only a trivial single-group job runs inline; everything else
  — including a job whose size cannot be determined — runs in the background and reports via
  the usual toast/webhook. Regression tests: `tests/test_sync_dispatch.py`.

  Note: background runs still occupy a worker for their duration. Moving matching off the
  uWSGI event loop entirely (Celery dispatch and/or cooperative yields) is tracked as
  follow-up work.

## v1.26.1861801 (July 5, 2026)

### Added
- **Auto-match after M3U refresh (opt-in).** New `Auto-match after M3U refresh`
  setting. When enabled, Stream-Mapparr runs Match & Assign automatically after
  each Dispatcharr M3U refresh (subscribes to the `m3u_refresh` connect event via a
  hidden `on_m3u_refresh` action). Requires a Profile to be selected. Per-account
  refresh events are coalesced: the match runs synchronously under a cross-worker
  `fcntl.flock`, and a dirty-flag marker triggers a rerun so accounts that finish
  during an in-flight match are not dropped (the loop always terminates, since
  refresh events are finite). Inert on Dispatcharr < 0.27, so the minimum version
  is unchanged.

## v1.26.1820605 (July 3, 2026)

### Strip invisible zero-width characters before matching (bug-105 / issue #36)

A user's IPTV provider injected a box-block glyph (`▎`, U+258E) padded with **invisible
zero-width characters** into stream names (e.g. `UK ␣<ZWSP>▎<ZWSP>BBC 1 FHD`), and almost
nothing from that provider matched. The visible block glyph was already dropped, but the
surrounding zero-width chars were not: `normalize_name` never removed Unicode **format
characters** (category `Cf` — ZERO WIDTH SPACE U+200B, ZWNJ/ZWJ, WORD JOINER U+2060, BOM
U+FEFF, SOFT HYPHEN, bidi marks). They are not matched by `\s` and are not in
`_DECORATOR_CATS`, so they survived the whole pipeline (output `"​ ​BBC 1"`) and
poisoned the fuzzy match. Pasting the character into **Ignore Tags** could not fix it.

- **Fix (`matching_core.py`).** Added a category-`Cf` strip at the very top of
  `normalize_name`: `name = ''.join(c for c in name if unicodedata.category(c) != 'Cf')`.
  Characters are **removed** (not replaced with a space) because they are zero-width — a
  ZWSP inside `BB<ZWSP>C` must yield `BBC`, not `BB C`. Edited the canonical
  `_shared/matching_core.py` and re-vendored byte-identically to all four plugins;
  **golden baseline unchanged** (the corpus contains no `Cf` chars), parity gates green.
- Non-ASCII letters (Cyrillic `Россия`, `beИN`) are category `Lu`/`Ll`, untouched.

### OTA common-word callsign false positives — fix + core hardening (bug-098, landed on main June 29, 2026)

The OTA channel `NBC - WA Seattle (KING)` was being assigned unrelated streams whose
names merely contain the word "king": `24/7 KING OF THE HILL`, `THE KING OF QUEENS`,
`WOLF KING` (and the same class for `WHO` → `DOCTOR WHO`, `WOLF` → `TEEN WOLF`). Root
cause: a common English word that is also a real US callsign (KING/WHO/WOLF/WAVE/WOOD/
WEEK/WILL) is rescued out of `_CALLSIGN_DENYLIST` once its station loads, and the OTA
stream-assignment loops matched streams with a bare callsign **word-grep** rather than the
confidence ladder.

- **Stream-side corroboration (`plugin.py`).** New `_callsign_needs_corroboration` +
  `_callsign_corroborated`: when an OTA channel's callsign is a common word, a candidate
  stream must corroborate the station — a parenthesized `(KING)` / suffixed `KING-TV` form,
  or the FCC `network_affiliation` (e.g. `NBC`), or `community_served_city` (e.g. `SEATTLE`).
  Both OTA assignment paths (`_match_streams_to_channel`, `match_us_ota_only`) apply it.
  Non-common-word callsigns (WFTV/WABC) keep the fast bare-word path.
- **Channel-side gate (`plugin.py`).** `_resolve_ota_callsign` now requires a common-word
  callsign to be a HIGH-confidence extraction (parenthesized/suffixed/end) before a channel
  is treated as OTA — so a non-station channel like `24/7 KING OF THE HILL` no longer resolves
  to KING-TV and grabs its streams; it falls through to fuzzy matching on its own name.
- **Shared core hardening (`matching_core.py`).** `_compute_callsign_with_confidence` no longer
  rescues a denylisted word at end-of-name (P3), and rescues it at the loose path (P4) ONLY in
  OTA branding context — immediately followed by a channel number (new `_OTA_NUMBER_CONTEXT`).
  Branded `KING 5` / `WAVE 3` / `WOOD TV8` / `WHO 13` survive; `King of the Hill` / `Doctor Who`
  / `Wolf King` extract nothing. Edited the canonical `_shared/matching_core.py` and re-vendored
  byte-identically; **golden baseline unchanged** (no regen). This makes the core robust by
  default so a future re-vendor that drops a subclass override can't reintroduce the bug.

Validated against 18,893 live streams + the live channel assignments; regression tests added
to `tests/test_ota_callsign_fallback.py`. Merged to `main` as PR #39 (`1.26.1802306`).

---

## v1.26.1791529 (June 29, 2026)

### Shared matcher core migration (landed on main June 28, 2026)

The copy-pasted, drifting `fuzzy_matcher.py` is retired. The pure matching primitives
(`normalize_name`, `calculate_similarity`, `process_string_for_matching`, the callsign
ladder, the regex tables) now live **once** in a shared core, `<workspace>/_shared/
matching_core.py`, vendored **byte-identically** into the inner folder as `matching_core.py`.
This plugin's `FuzzyMatcher` now **subclasses `FuzzyMatcherCore`** and keeps only its
plugin-specific layer (zone routing, OTA callsign resolution, channel-DB loading).
Stream-Mapparr was the migration canary. Matcher fixes now go to the shared core (edit
`_shared/matching_core.py`, re-vendor with `scripts/sync_core.py`, regenerate the golden
baseline if behavior changed) — no more hand-porting a fix to four copies.

- **Hash-pinned + gated.** The vendored core is sha256-pinned in `scripts/core_manifest.json`
  and enforced by a parity gate (`tests/test_core_parity.py`) plus a golden gate
  (`tests/test_matcher_golden.py`) in CI; `.gitattributes` pins `matching_core.py` to LF so a
  CRLF checkout can't change the hash. `matching_core.py` is kept out of `bump_version.py`
  stamping. **Deploy `matching_core.py` with the code** — `fuzzy_matcher.py` imports
  `FuzzyMatcherCore` from it, so the plugin won't load without it.
- **`strip_bare_region` opt-in.** Stripping bare time-zone region words (Pacific/Central/
  Mountain/Atlantic) for scoring is now a class-attr opt-in (`_STRIP_BARE_REGION`); the core
  default leaves them in, preserving Stream-Mapparr's bug-066 behavior (off here). Lineuparr /
  EPG-Janitor opt in.
- **`calculate_similarity` `>= min_ratio` gate.** The early-termination parameter is now
  `min_ratio`, and rapidfuzz's `score_cutoff` is replaced by a Python `>= min_ratio` gate
  applied identically on both code paths (a score landing exactly on the cutoff is kept;
  below-cutoff is zeroed on both). No change to live match decisions.

### Matching & normalization (landed on main June 25, 2026)

Three matcher robustness fixes ported from Lineuparr (PR #13). `calculate_similarity`
was NOT touched: Stream-Mapparr was already canonical (`1 - distance / max(len)`,
rapidfuzz `Levenshtein.normalized_similarity`) and is the reconciliation reference
alongside EPG-Janitor.

- **Non-ASCII names no longer collapse to empty in `process_string_for_matching`.**
  The matching-string builder NFKD-folds, then keeps any `char.isalnum()` (alphanumerics
  of any script) instead of the old ASCII-only `a-z0-9` filter. Cyrillic / CJK / Arabic
  names previously reduced to `''`, which compared as a false **100% match** against any
  other all-non-ASCII name; they now survive and match on their actual characters.

- **Leading box-bar bouquet tags are stripped in `normalize_name`.** A new
  `_LEADING_BAR_TAG_RE` removes a leading `┃…┃` / `│…│` provider/bouquet tag
  (`"┃CANAL+┃ NPO 1"` → `"NPO 1"`). Box bars never occur in real channel names, so the
  strip is always safe and also covers leading `┃XX┃` country/source tags.

- **Box-bar delimiters added to `GEOGRAPHIC_PATTERNS`.** `┃` (U+2503) and `│` (U+2502)
  are now accepted as colon-equivalents after a 2-3 letter code (`"NL┃ NPO 1"` → `"NPO 1"`)
  and as matched pairs (`┃XX┃` / `│XX│`, matched pair only so a stray `|US┃` is left alone).
  Stream-Mapparr has no `PROVIDER_PREFIX_PATTERNS` list, so only `GEOGRAPHIC_PATTERNS` changed.

Full local suite green: **243 passed**.

---

## v1.26.1761110 (June 25, 2026)

**Type**: Packaging hotfix. No functional code change — a version-only
re-release of v1.26.1751210 with a correctly-built release zip.

### Fixed

- **Install fails with "missing plugin.py or package __init__.py" (bug-087).**
  The v1.26.1751210 release zip was packaged on Windows with a tool that writes
  ZIP entry paths using backslash (`\`) separators (PowerShell `Compress-Archive`
  / .NET Framework `ZipFile.CreateFromDirectory`). The ZIP spec mandates forward
  slashes; on Dispatcharr's Linux host the backslash is a literal filename
  character, so every entry extracted as a flat file (`Stream-Mapparr\plugin.py`)
  with no real package directory — failing both direct installs and the Plugin
  Hub (external `source_url` mode re-hosts this zip, and only re-fetches on a
  version bump, so a new version was required to fix the marketplace). The zip is
  now built with forward-slash separators and verified before upload.

### Added

- **`scripts/validate_zip.py` + CI gate.** A release-zip validator that parses the
  raw central-directory bytes for backslash separators (Python's `zipfile`
  normalizes them away on read, hiding the defect), confirms the package root, and
  rejects dev junk. Wired into `.github/workflows/ci.yml` to validate any committed
  release zip. The same guard was ported across the plugin cohort.

---

## v1.26.1751210 (June 24, 2026)

**Type**: Feature + bugfix release. Zone-aware East/West stream routing and a
multi-worker scheduler-dedup fix — both verified live on the container (the
scheduled run was test-fired and confirmed to run exactly once). Two independent
QA passes plus a `/simplify` quality pass across the changes.

### Fixed

- **Scheduled run no longer fires once per worker (bug-069).** Dispatcharr runs
  several worker processes; after the bug-065 fix each armed a scheduler, so the
  05:00 job ran N times (observed: 5 sort CSVs in one minute). Ported the proven
  cross-worker pattern from event-channel-managarr: a shared last-run file
  (`/data/stream_mapparr_scheduler_last_run.json`, `{HH:MM → date}`) plus an
  `fcntl.flock` guard (`/data/stream_mapparr_scheduler.lock`) that makes the
  read-check-stamp atomic. Only the worker that wins the slot runs the job; the
  rest skip before doing any work. (QA: the lock fd is closed if `flock` raises
  after `open` succeeds; a claim-loser skips only its slot, not the whole loop.)
  Harmless before for deterministic Sort, but required before scheduled Match &
  Assign (concurrent ORM writes). Degrades to a plain check-and-stamp on non-POSIX
  hosts (tests/dev). Live-verified: a test-fired slot showed one trigger + one
  "skipping duplicate run" + one CSV.

### Added

- **Zone-aware East/West stream routing (bug-068).** Distinct feeds like
  "Starz Encore" and "STARZ Encore (W)" no longer just share one quality-sorted
  pool — each routed channel's streams are re-ordered so the West channel's
  primary is a West feed and the East/default channel's primary is an East/generic
  feed. `FuzzyMatcher.extract_zone()` derives WEST/EAST/DEFAULT from `(W)`/`(E)`/
  `(WEST)`/`(EAST)` and bare `WEST`/`EAST` words. Routing activates **only** for
  channels that share a zone-stripped base name with a different-zone sibling, so
  the common single-feed case is unchanged. Zone siblings that collapse into one
  group (same-case names) bypass `visible_channel_limit` so each zone still gets a
  channel.

  Applied consistently across **Match & Assign**, **Sort Alternate Streams**, and
  **Preview** (via a shared `_streams_for_channel` seam) — so Preview reflects the
  per-channel order the real run applies, and Sort no longer reverts the zone
  preference. _Note:_ routing **re-orders** the matched pool (per the chosen "West
  never empty, falls back to generic/East" policy) rather than partitioning it, so
  both channels carry the full pool as alternates; **Pacific** folds into the
  DEFAULT (East-like) bucket.

---

## v1.26.1742332 (June 23, 2026)

**Type**: Bugfix + feature release — restores the background scheduler, fixes a
matcher over-strip that merged distinct channels, adds IPTV-Checker-style
progress/results visibility with live notifications, and fixes Discord/Slack
webhook delivery (issue #32).

### Fixed

- **Background scheduler now actually runs (bug-065).** The scheduler armed only
  from `on_load()`, which this Dispatcharr build never invokes — while it
  re-instantiates the `Plugin` constantly. Scheduled runs had silently stopped
  for days (no `Background scheduler started` / `Scheduled scan triggered` in the
  logs; only manual runs). It now bootstraps from `Plugin.__init__()` via
  `_load_settings()`, mirroring the working `event_channel_managarr` sibling. The
  stop→start sequence is serialized with a re-entrant `_scheduler_lock` so
  concurrent construction can't orphan a scheduler thread (would otherwise
  double-fire a scheduled scan).

- **Distinct channels no longer collapse onto one another (bug-066).** Bare
  timezone words ` Pacific`/` Central`/` Mountain`/` Atlantic` were stripped as
  feed markers in `REGIONAL_PATTERNS`, so "Comedy Central" → "Comedy" (collided
  with "Comedy TV") and "The Atlantic" → "The". As bare words these are brand
  tokens far more often than US timezone feeds — they are removed from the bare
  set. Bare `East`/`West` (the canonical dual-feed suffixes) and all
  parenthesized timezone tags (`(Central)`) are kept. Surfaced from a live-DB
  audit where Starz Encore East/West and Comedy Central/Comedy TV shared
  identical stream pools.

- **Discord & Slack webhooks deliver again (bug-067 / issue #32).** Completion
  webhooks POSTed a custom JSON object, which Discord and Slack reject (HTTP 400)
  because the body lacks their required key. `_build_webhook_body` now sends the
  native shape — `{"content": …}` to Discord, `{"text": …}` to Slack
  (host-anchored detection, truncated to platform limits) — while every other URL
  keeps the generic JSON payload unchanged.

### Added

- **📊 View Check Progress** and **📋 View Last Results** actions. Progress
  (action, %, ETA) is persisted to `/data/stream_mapparr_progress.json` and the
  last completed run's summary to `/data/stream_mapparr_last_results.json` (atomic
  writes), so the view actions read accurate live/last state even though each
  action call may hit a fresh `Plugin` instance.

- **Live progress notifications.** Long-running operations now emit a "started"
  toast, periodic `% + ETA` toasts at an adaptive cadence (3s/5s/10s by job
  size), and a completion toast — generic across Match & Assign, Sort, Probe,
  OTA, and Visibility. `load_process_channels` is treated as an internal sub-step
  (no duplicate toasts).

- ~70 regression tests across `tests/test_progress_notifications.py`,
  `test_fuzzy_matcher.py`, and `test_plugin_helpers.py` (suite now 226).

---

## v1.26.1720023 (June 21, 2026)

**Type**: Bugfix release — OTA (broadcast) callsign matching restored, and a
data-loss guard for overwrite runs. Fixes bug-063 (user-reported: running
Match & Assign against US: ABC/CBS/FOX/NBC left almost every channel with no
streams).

### Fixed

- **Overwrite no longer wipes streams on a zero-match run.** In
  `add_streams_to_channels`, a channel that matched 0 streams while
  `overwrite_streams=True` previously had all its existing `ChannelStream`
  rows deleted and nothing assigned — so a run that matched nothing (wrong
  threshold, a database/callsign gap) silently emptied every channel.
  "Overwrite" now only replaces when there are actual replacement streams;
  an empty match set leaves existing streams untouched.

- **OTA affiliates match by callsign again.** OTA detection in
  `_match_streams_to_channel` depended solely on a `US_channels.json` database
  lookup, but that database carries no `broadcast`/`callsign` entries — so
  affiliates like `ABC - AL Montgomery (WNCF)` fell through to strict fuzzy
  name matching and matched nothing. New `_resolve_ota_callsign()` resolves the
  callsign from the FCC station table first, then falls back to a
  parenthesized callsign in the Dispatcharr channel name.

### Added

- **FCC station table (`networks.json`) is now loaded and used.** Stream-Mapparr
  already shipped `networks.json` (1915 US OTA stations: callsign → network /
  city / state) but never loaded it. `FuzzyMatcher._load_broadcast_stations()`
  (ported from Channel-Maparr) now indexes it into `channel_lookup` on every
  load, giving authoritative callsign validation. `_build_us_callsign_database`
  (used by `Match US OTA Only`) is repointed from the empty `US_channels.json`
  to `networks.json`, reviving that action. **`networks.json` is now part of the
  deploy file set** (it was missing from the container entirely).

- 19 regression tests in `tests/test_ota_callsign_fallback.py`.

---

## v1.26.1650116 (June 13, 2026)

**Type**: Maintenance — plugin manifest cleanup (no runtime behavior change).

### Internal

- **`plugin.json` settings manifest de-drifted.** The static `fields` array had gone stale: it still listed the removed `timezone` setting and was missing `custom_aliases` plus the audio / throughput / webhook fields. `plugin.py` defines settings dynamically via the `Plugin.fields` property (the single source of truth, which Dispatcharr always uses when present), so the static array is now intentionally `"fields": []` with an explanatory `_fields_note` — mirroring the Lineuparr convention so it can't drift again. No runtime or settings-form change; the published Plugin Hub entry now shows accurate metadata.

---

## v1.26.1650009 (June 13, 2026)

**Type**: Feature + bugfix release — matcher robustness (stylized-Unicode / emoji / resolution-marker normalization), Phase-1 channel-name alias matching, Dispatcharr-sourced timezone, multi-source stream dedup, CSV source labeling, plus the project's first automated test suite & CI. Consolidates the work shipped across `1.26.1641824`–`1.26.1650009`.

### Matching & normalization

Three normalization passes were added to the top of `FuzzyMatcher.normalize_name` so stylized stream names match their plain channel names. Each was validated by an old-vs-new corpus diff over the real ~54k-name stream pool **and** all channel databases, asserting **0 harmful changes** (no real ASCII or non-Latin name altered).

**Stylized-Unicode decoration stripping** (`bug-048`):
- Streams decorate names with stylized-Unicode tier/format markers — superscripts (`RK: WEATHERNATION ᴿᴬᵂ`, `… ⁶⁰ᶠᵖˢ`, `… ⱽᴵᴾ`, `… ³⁸⁴⁰ᴾ`), Latin small-caps (`… ꜰʜᴅ`), and bullets (`◉`). The ASCII tag regexes couldn't see them, so `RK: WEATHERNATION ᴿᴬᵂ` never matched channel **WeatherNation** (0 matches).
- **Fix**: `normalize_name` now drops whole tokens that are pure stylized decoration, then NFKD-canonicalizes the rest. Decoration is detected by Unicode character **name** (`SUPERSCRIPT` / `SUBSCRIPT` / `SMALL CAPITAL` / `MODIFIER LETTER`), **not** hard-coded code-point ranges — real markers fall outside the obvious blocks (small-cap `H`=U+029C, modifier `V`=U+2C7D). Collision-safe (ASCII `Gold`/`VIP` untouched) and non-Latin-safe (Arabic/Cyrillic/CJK preserved). Runs unconditionally; punctuation-glued ornaments (`◉:`, superscript `HD/RAW`) are handled too.

**Emoji-as-letter substitution** (`bug-051`):
- Some streams use an emoji **as a letter**: `beIN SP⚽RTS` / `Sp⚽rts` (the soccer ball stands in for `o` = SPORTS, the beIN family, ~682 names). Previously the ball became a space (`sp rts`) and never matched `sports`.
- **Fix**: `normalize_name` maps an emoji to the letter it replaces **only when flanked by ASCII letters** (`SP⚽RTS`→`SPORTS`), and strips emoji used purely as decoration (`♬`, `☾`, standalone `⚽`, and zero-width `U+FE0F`/ZWJ). Recovers the base `beIN Sports` feed when its streams are present in the selected sources.

**Numeric resolution-marker stripping** (`bug-055`):
- Resolution tags the keyword quality patterns missed — `3840P`, `2160P`, `1080P`/`1080i`, `720P` — are now stripped via `RESOLUTION_PATTERNS` (`\b\d{3,4}[pi]\b`, gated by quality stripping, applied **before** the keyword patterns to avoid space-gluing). Requires the `p`/`i` glued to the digits, so bare numbers and single-digit channel numbers (`Channel 4`, `Studio 1080`) are untouched.

**Matcher score depended on whether `rapidfuzz` was installed** (`bug-026`):
- `FuzzyMatcher.calculate_similarity` had two implementations — a rapidfuzz fast path returning `1 - distance / max(len)`, and a pure-Python fallback returning `(len1 + len2 - distance) / (len1 + len2)`. They disagreed: at threshold 95, `Fox Sports 1` vs `Fox Sports 2` scored **0.917** (rapidfuzz) vs **0.958** (pure-Python), flipping the match decision.
- **Fix**: the pure-Python branch (and its early-termination bounds) now use `1 - distance / max(len)`, matching rapidfuzz exactly. Production runs the rapidfuzz path, so live behavior is unchanged — only the no-rapidfuzz fallback was corrected. Enforced by an automated parity test.

### Features

- **Phase-1 channel-name alias matching**: an exact-normalized alias layer (`FuzzyMatcher.alias_lookup` + a built-in US alias table) force-includes known aliases into a channel's matches, independent of the fuzzy threshold. A new **Custom Aliases** setting accepts a JSON object of additional `"channel": ["alias", …]` mappings (merged with the built-ins; invalid entries are logged and skipped).
- **Timezone now follows Dispatcharr's global setting**: the plugin's own *Timezone* dropdown was **removed**. Scheduled runs read the timezone from Dispatcharr (`CoreSettings.get_system_time_zone()`), validated via `pytz`, with a `UTC` fallback. One less setting to keep in sync.
- **CSV stream-source labeling**: every stream name in a CSV export is now tagged with its M3U source (e.g. `GO: CNN [streamq.tv-bk15]`), so identical names from different providers are distinguishable in reports.
- **Multi-source stream dedup** (GitHub #28 / #29): deduplication is keyed on `(name, m3u_account)`, so the same channel name from **different** providers both survive (multi-source failover); only true same-source duplicates collapse. Runs after the quality sort, so the kept copy is the best one.
- **Norwegian (NO) channel database** (GitHub #30).

### Internal & tooling

- **First automated test suite** (`tests/`, **176 passing**): `fuzzy_matcher` matching/normalization (incl. the three new normalizers, with collision/non-Latin guards), `plugin.py` pure helpers (via a Django-stubbing conftest), channel-database schema validation, and version-sync. Cases are regression locks derived from the bug history — every fix above ships with one.
- **CI** (`.github/workflows/ci.yml`): py_compile + version-sync + database validation + pytest on every push/PR, with a least-privilege `permissions` block and `pytz` installed for the timezone tests.
- **Pre-commit gate** (`.githooks/pre-commit`, opt-in) and helper scripts (`scripts/check_version_sync.py`, `scripts/validate_databases.py`).
- **`docs/DEVELOPMENT.md`** + design specs/plans under `docs/`.
- **Deploy process corrected**: `plugin.json` mtime hot-reload proved unreliable in practice — always `docker restart dispatcharr` after copying, and copy **every** changed file (incl. `fuzzy_matcher.py` and new modules like `aliases.py`).

### Notes
- `fuzzy_matcher.py` bumped to **v26.165.0009**.
- The three normalization fixes are matcher-shared; port guides for the sibling plugins (Channel-Maparr, EPG-Janitor, Lineuparr, Metadata-Trackarr) are kept as local `MATCHER-NORMALIZATION-PORT.md` references.

---

## v1.26.1511211 (May 31, 2026)
**Type**: Bugfix release — numeric-sibling false-positive in fuzzy matcher.

### Bugfix

**Same-prefix numbered channels false-match at threshold 95** (e.g. `Fox Sports 1` pulling in `Fox Sports 2` streams):
- Under Stage 3 token-sort Levenshtein, the discriminating digit is a single-character edit. With long shared prefixes the score sails past threshold — `"1 fox sports"` vs `"2 fox sports"` is edit-distance 1 over total-length 26 = **96.15%**, above the default 95.
- A numeric-token discriminator guard already existed inline in `plugin.py:2329` but not in the two public `FuzzyMatcher` methods, so every caller routed through `fuzzy_match` / `find_best_match` was unprotected.
- **Fix**: mirror the guard into `fuzzy_matcher.fuzzy_match` (all three stages) and `fuzzy_matcher.find_best_match`. When the normalized query contains digit-only tokens, candidates must (a) have at least one digit token and (b) share at least one with the query. Queries without digits are unconstrained (no behavior change for the common case).
- Math sanity: `Channel 4` vs `Channel 4K` is safe because `4K` is stripped by quality patterns before this code sees it. `ESPN 2 HD` vs `ESPN HD` (digit-asymmetric) now correctly rejects.

### Notes
- `fuzzy_matcher.py` bumped to **v26.151.1208** (was 26.095.0100).
- **Stale data caveat**: pre-existing wrong assignments from earlier runs (ESPN2/ESPN+, C-SPAN2/C-SPAN3, FS1/FS2, Discovery Turbo +1) are not cleaned up by this fix. Run `add_streams_to_channels` with `overwrite_streams=true` to re-match and replace polluted assignments.
- QA-reviewed (`pr-review-toolkit:code-reviewer`): zero blocking findings.

---

## v1.26.1362122 (May 16, 2026)
**Type**: Feature + bugfix release — audio-aware stream sorting (fixes GitHub #27) and profile-dropdown loading fix (fixes GitHub #26).

### Bugfixes

**Cannot load channels — "validation failed" when no profiles exist** (fixes GitHub #26):
- The Channel Profile dropdown built a placeholder option with a blank `value` when the Dispatcharr instance had zero `ChannelProfile` rows. Dispatcharr's plugin-field serializer rejects blank option values (`This field may not be blank`) and **dropped the entire `profile_name` field**, so affected users could never select a profile and every run failed with an opaque "Cannot load channels - validation failed."
- The placeholder now uses a non-blank `_none` sentinel; all five `profile_name` read paths (`_validate_plugin_settings`, `load_process_channels`, the secondary load path, `sort_streams`, `probe_throughput`) normalize `_none` back to "not configured".
- `load_process_channels` now surfaces the specific failed validation check (e.g. `Cannot load channels - Profile Name: Not configured`) instead of the generic message.

### Features

**Audio priority dimensions in the quality sort** (addresses "two equal-resolution streams, one 5.1 one stereo" — surround should win):
- Two new opt-in settings, each a comma-separated list ordered most-preferred-first, left to right:
  - `audio_channels_priority` (e.g. `7.1, 5.1, stereo, mono`)
  - `audio_codec_priority` (e.g. `eac3, ac3, aac, mp2`)
- Matching is **case-insensitive substring**. Anything not listed (or with missing audio info) sorts last.
- Audio is factored into `_sort_streams_by_quality` **after** the video resolution/FPS tier and **before** the pixel/FPS tiebreaker. Channel layout is ranked **before** codec.
- Data source is `Stream.stream_stats` (`audio_channels` / `audio_codec`, populated by IPTV Checker) — no probing is performed.

### Notes
- Both settings default to empty (disabled). When blank, the sort is unchanged — **no behavior change on upgrade**.
- Internal: priority-list parsing delegates to the existing quote-aware `_parse_tags` helper instead of a duplicate splitter.

---

## v1.26.1171629 (April 27, 2026)
**Type**: Bugfix series after the v1.26.1171458 throughput-sort feature went live.

### Bugfixes (rolled up from 1171545 → 1171547 → 1171558 → 1171604 → 1171629)
- **`timezone.utc` removed in Django 5** — the probe action used `datetime.now(timezone.utc)` from `django.utils.timezone`. Switched to `timezone.now()` (Django's aware-UTC) and aliased the stdlib's `datetime.timezone` as `dt_timezone` for the `_is_probe_fresh` parser. Same latent bug exists at `_fire_webhook` line 2543 — left for a follow-up since it doesn't fire today.
- **Probe scope ignored `selected_groups`** — clicking *Probe Stream Throughput* with `Movies` selected pulled all 3,425 streams in the profile (45-minute estimate). Probe action now narrows to `Channel.objects.filter(channel_group__name__in=...)` like the other actions.
- **`UserAgent` model passed to `urllib.Request`** — `M3UAccount.user_agent` is a ForeignKey to a `UserAgent` row, not a string. The helper now dives into `.user_agent / .value / .string / .name` on the related instance to extract the actual UA.
- **Failed probes locked re-probing for a TTL window** — `_is_probe_fresh` returned True when a cache entry had a fresh timestamp but `throughput_mbps == None`. After the all-failures run, the entire group was un-re-probable for 30 minutes. Now: any null-mbps entry is treated as never-fresh.
- **Tiny-fast reads no longer report fake 0 Mbps** — if a probe returns under 64 KB in under 1 second, we record null instead of computing a tiny denominator-driven Mbps. Also prepares for HLS-aware probing (`.m3u8` playlists fall in this band today).
- **Probe failures elevated to WARNING** — exception class + message are logged so an "all probes failed" run is diagnosable from normal log output.
- **Sort CSV gains `tiers`, `throughput_mbps`, `edge_ips` columns** — semicolon-joined, indices aligned with `stream_names`. Lets you eyeball which sources got demoted by throughput vs which were simply lower resolution.

### Verified end-to-end on real STL OTA streams
- 33 streams probed, 31 measured. Edge IPs visible (`206.53.1.100`, `213.178.142.x`, etc.).
- KDNL (ABC) had three sources at 0.00 / 8.81 / 13.05 Mbps on three different edges — the broken one was correctly demoted from equal-rank to `insufficient` in the live sort.
- KMOV (CBS): 5 healthy + 1 marginal + 1 unknown + 3 insufficient — tiers slot in exactly as the spec describes.

---

## v1.26.1171458 (April 27, 2026)
**Type**: Feature Release — throughput-based stream sorting.

### Features

**Measured-throughput sort dimension** (addresses the "two 720p60 streams, wildly different real bitrate" problem):
- New `probe_throughput` action: opens a short HTTP GET to each stream currently assigned to a channel in the selected profile, sums bytes over a fixed window (default 8s), and records Mbps + final-URL host (`edge_ip`) in `/data/stream_mapparr_throughput_cache.json`.
- Probes are **serialized per M3U account** with a 1-second per-account gap and a global cap of `probe_rate_per_minute` (default 6).
- Cached probes are reused for `probe_cache_ttl_minutes` minutes (default 30); only stale or missing entries are re-probed.
- A tier dimension is **prepended** to the existing `_sort_streams_by_quality` sort key:
  - `healthy` (≥ nominal × 1.5), `marginal` (≥ nominal × `bitrate_safety_margin`), `unknown` (no fresh probe), `insufficient` (below margin).
  - When all candidates are `unknown`, the prepended dimension collapses and the existing resolution/FPS/M3U-priority order is preserved — feature degrades cleanly if no probes have run.
- Nominal bitrate is estimated from `stream_stats.width/height/source_fps` against a heuristic table (1080p60 ≈ 6 Mbps, 720p60 ≈ 4, etc.) — `PluginConfig.NOMINAL_BITRATE_TABLE`.

### New settings (additive; defaults preserve current behavior when probes haven't run)
- `enable_throughput_sorting` (bool, default `true`)
- `probe_duration_seconds` (default `8`)
- `probe_cache_ttl_minutes` (default `30`)
- `probe_rate_per_minute` (default `6`)
- `bitrate_safety_margin` (default `1.10`)

### Notes
- Probing is opt-in per run via the new action button — sort never blocks on a probe; it always reads the cache.
- Cache file: `/data/stream_mapparr_throughput_cache.json`. Per-stream entry: `{throughput_mbps, throughput_measured_at, edge_ip, nominal_bitrate_mbps, probe_duration_s}`.
- Real-time mid-stream degradation detection is explicitly **out of scope** — that's a ts_proxy concern.

---

## v1.26.1082140 (April 18, 2026)
**Type**: Feature + Performance + UX Release. Version scheme switches to calver (`1.MAJOR.DDDHHMM`, UTC day-of-year + HHMM) to match the Lineuparr / Channel-Mapparr / EPG-Janitor / IPTV Checker cohort. Use `Stream-Mapparr/bump_version.py` to keep `plugin.json` and `plugin.py` versions in sync.

### Features

**Zone-based channel variants** (closes #25):
- Channel JSON databases support a new `"zones": ["East", "West"]` array on premium channels.
- The loader expands each zoned entry into per-zone variants at load time (`FX` → `FX East`, `FX West`) via `FuzzyMatcher._expand_zones`.
- Non-list / empty / duplicate zone values are handled safely (warning logged; case-insensitive dedup).
- 33 major US cable networks pre-populated with East/West zones: FX, FXX, FXM, USA Network, Syfy, TBS, TNT, Comedy Central, A&E, AMC, Disney Channel, Disney XD, Cartoon Network, Nickelodeon, MTV, VH1, HGTV, Food Network, History, TLC, Lifetime, Bravo, E!, Freeform, Paramount Network, BET, CMT, Animal Planet, National Geographic, Oxygen, Travel Channel.
- Use **Tag Handling → Keep Regional Tags** + **Visible Channel Limit ≥ 2** to keep the zones distinct during matching.

**Country-restricted matching** (opt-in, new `restrict_matching_to_country` setting):
- When enabled, a channel only matches streams whose detected country matches the channel's group or name.
- Covers all 11 shipped country DBs with a unified alias dictionary (US, UK, CA, AU, IN, DE, FR, NL, ES, MX, BR).
- Detection handles `[US]` bracket prefixes, `USA: / USA-` punctuation prefixes (not whitespace, to avoid "IN THE NEWS" → India), and full country-name substring matching.
- Two-letter aliases only detect via bracket/prefix forms to prevent English-word collisions.

**Webhook completion notifications** (new `webhook_url` + `fire_webhook_on_completion` settings):
- POSTs a JSON summary (plugin, event, action, status, message, timestamp, counts, CSV basename, dry_run flag) to any HTTP(S) endpoint on action completion.
- Fires in a daemon thread — does not block the action return path.
- Reserved payload keys (`plugin`, `event`, `action`, `status`, `message`, `timestamp`) are never clobbered by caller-supplied details.
- Failures are logged as warnings; webhook delivery never masks a successful matching run.

### Performance

- **`bulk_create` for all ORM write paths** (`add_streams_to_channels`, `match_us_ota_only`, `sort_streams`). Collapses N serial `INSERT` round-trips into one query per channel — ~100× speedup on the write phase.
- **CSV export reuses cached match results** from the main matching loop instead of re-running the full fuzzy-match pipeline + threshold variants. Previous bottleneck was ~108 redundant matches per action; now zero.
- **ETA constant recalibrated** from `0.1 s/item` to `0.8 s/item` based on observed rapidfuzz timing (14s for 18 channels × 19k streams).
- **Hybrid sync/background dispatch**: jobs estimated under 25 seconds run synchronously from `run()` so the Dispatcharr Mantine toast fires with the real completion message. Larger jobs stay background with webhook/WebSocket signalling. Threshold chosen to stay under gunicorn's 30s worker timeout with headroom for `load_process_channels` prelude + ORM + CSV.

### UX

- Action buttons in `plugin.json` and the class-level `Plugin.actions` list gain `button_variant`, `button_color`, `button_label`, and `confirm` dialogs matching the `iptv_checker` pattern.
  - Primary matching action: filled blue.
  - Semi-destructive (modifies channels/streams): orange.
  - Destructive (deletes data): red.
  - Read-only (Validate/Preview): outline blue.
- Completion notification messages tightened to single-line plain text — better fit for Mantine toasts than the previous multi-line banners.
- `manage_channel_visibility` now joins the sync-eligible set so its completion toast fires naturally.

### Fixes

- **Manual runs were incorrectly logged as `Scheduled`**. `add_streams_to_channels_action`'s positional signature `(settings, logger, is_scheduled=False, context=None)` was being called positionally as `(settings, logger, context)`, so the UI context dict was binding to `is_scheduled` (truthy). Now called with explicit `context=` keyword.
- **`validate_settings` and `_send_progress_update` no longer dump multi-line content into the Mantine notification** — full validation detail is routed to logs, notification gets a single summary line.

### Developer

- **`bump_version.py` added** — ports the helper from `iptv_checker`, adapted for Stream-Mapparr's `PluginConfig.PLUGIN_VERSION` class-attribute style. Auto-generates calver or accepts an explicit version argument; verifies `plugin.json` and `plugin.py` stay in sync.
- **`_get_all_channels` ORM fetch extended** to include `channel_group__name` (needed for country detection). Streams already had it.
- **Hot-reload caveat**: Dispatcharr v0.23.0+ reloads plugins on `plugin.json` mtime changes, NOT on `plugin.py` changes alone. `bump_version.py` updates both files' version strings, which bumps `plugin.json` mtime and triggers reload on `docker cp`.

## v0.9.0 (April 4, 2026)
**Type**: Performance & UI Enhancement Release

### Performance Optimizations (ported from Linearr plugin)

**Levenshtein Acceleration**:
- Uses `rapidfuzz` C extension when available (20-50x faster)
- Pure Python fallback with early termination via new `threshold` parameter
- Combined effect: matching 94 channels x 3,362 streams in ~2s (was ~5 minutes)

**Normalization Cache**:
- Added `precompute_normalizations()` to cache stream name normalization once before matching loops
- `fuzzy_match()` and `find_best_match()` use cached results via `_get_cached_norm()` / `_get_cached_processed()`
- Eliminates redundant `normalize_name()` calls across all 3 matching stages
- Cache fallback uses stored ignore flags for consistency

**ETA Calculation**:
- Updated `ESTIMATED_SECONDS_PER_ITEM` from 7.73s to 0.1s to reflect actual performance

### UI Simplification

**Profile Name**: Free-text input replaced with dynamic dropdown populated from database

**Match Sensitivity**: Numeric threshold (0-100) replaced with named presets:
- Relaxed (70), Normal (80), Strict (90), Exact (95)

**Tag Handling**: Four separate boolean toggles consolidated into single dropdown:
- Strip All Tags (default), Keep Regional Tags, Keep All Tags

**Channel Database**: Per-country boolean toggles consolidated into single dropdown:
- None, individual country, or All databases

All changes are backward compatible — legacy field IDs still work as fallbacks.

### Files Modified
- `fuzzy_matcher.py` v26.095.0100: Cache system, rapidfuzz support, early termination
- `plugin.py` v0.9.0: Precompute calls, UI fields, settings resolvers
- `plugin.json` v0.9.0: Updated field definitions

### Version Compatibility
| Plugin Version | Required fuzzy_matcher |
|---------------|------------------------|
| 0.9.0 | 26.095.0100+ |
| 0.8.0b | 26.018.0100+ |
| 0.7.4a | 26.018.0100+ |

---

## v0.8.0b (March 11, 2026)
**Type**: Bugfix Release
**Severity**: HIGH (ORM Migration)

### Bug Fixed: Invalid `group_title` Field on Stream Model

**Issue**: After migrating from HTTP API to Django ORM in v0.8.0a, the plugin used `group_title` as a field name on the Stream model. This field does not exist — the correct field is `channel_group` (a ForeignKey to `ChannelGroup`). Any action that loads streams (Add Streams, Preview Changes, Load/Process Channels) would fail with:
```
Cannot resolve keyword 'group_title' into field.
```

**Root Cause**: During the ORM migration, the old API response field name `group_title` was carried over into ORM `.values()` queries, but the Django model uses `channel_group` (FK) instead.

**Fix**: Replaced `group_title` with `channel_group__name` (Django FK traversal) in two locations:
- `_get_all_streams()`: Stream data query
- `_get_stream_groups()`: Distinct stream group name query

**Files Modified**:
- `plugin.py` v0.8.0b: Fixed ORM field references
- `plugin.json` v0.8.0b: Version bump

---

## v0.7.4a (January 18, 2026)
**Type**: Critical Bugfix Release
**Severity**: HIGH (Stream Matching)

### Bug Fixed: 4K/8K Quality Tags Not Removed During Normalization

**Issue**: Streams with "4K" or "8K" quality suffixes were not matching correctly because the space normalization step was splitting "4K" into "4 K" before quality patterns could remove it.

**Example**:
- Stream: `┃NL┃ RTL 4 4K`
- Expected: Tag removed → "RTL 4" → matches channel
- Actual: "4K" split to "4 K" → patterns fail → "RTL 4 4 K" → no match

**Root Cause**: The digit-to-letter space normalization (`re.sub(r'(\d)([a-zA-Z])', r'\1 \2', name)`) transformed "4K" into "4 K" before quality patterns could match and remove "4K".

**Pattern Observed**:
| Quality Suffix | Affected? | Reason |
|---------------|-----------|--------|
| HD, SD, FHD, UHD | No | All letters, not split |
| 4K, 8K | **Yes** | Digit+letter split to "4 K", "8 K" |

**Fix**: Quality patterns are now applied BEFORE space normalization to prevent "4K"/"8K" from being broken.

**Files Modified**:
- `fuzzy_matcher.py` v26.018.0100: Moved quality pattern removal before space normalization
- `plugin.py` v0.7.4a: Updated version and minimum fuzzy_matcher requirement

---

## v0.7.3c (December 23, 2025)
**Type**: Critical Bugfix Release
**Severity**: HIGH (Unicode tag users)

### Bug Fixed: Custom Ignore Tags with Unicode Characters Not Working

**Issue**: Custom ignore tags containing Unicode or special characters (like `┃NLZIET┃`) were completely ignored during normalization, causing all channels to fail matching.

**Root Cause**: Code used regex word boundaries (`\b`) for all custom tags. Word boundaries only work with alphanumeric characters. Unicode characters like `┃` (U+2503) are not word characters.

**Fix**: Smart tag detection - only use word boundaries for pure alphanumeric tags, use literal matching for Unicode/special character tags.

---

## v0.7.4 (December 22, 2025)
**Type**: Critical Bugfix Release
**Severity**: HIGH (Matching Accuracy)

### Bug #1 Fixed: Substring Matching Too Permissive

**Issue**: "Story" matched "HISTORY" at threshold 80 because substring matching didn't validate semantic similarity.

**Fix**: Added 75% length ratio requirement for substring matches.

### Bug #2 Fixed: Regional Tags Stripped Despite Setting

**Issue**: `Ignore Regional Tags: False` didn't work - "(WEST)" was still being removed by MISC_PATTERNS and callsign patterns.

**Fix**: Conditional MISC_PATTERNS application and callsign pattern with negative lookahead for regional indicators.

---

## v0.7.3 (December 21, 2025)
**Type**: Enhancement Release

### Added FuzzyMatcher Version to CSV Headers

CSV exports now show both plugin and fuzzy_matcher versions for better troubleshooting:
```csv
# Stream-Mapparr Export v0.7.3
# FuzzyMatcher Version: 25.354.1835
```

---

## v0.7.2 (December 20, 2025)
**Type**: Bugfix Release

### Fixed: Incomplete Regional Patterns

Updated fuzzy_matcher dependency to include all 6 US timezone regional indicators (East, West, Pacific, Central, Mountain, Atlantic) instead of just "East".

---

## v0.6.x Series

### v0.6.17 - M3U Source Prioritization
Added M3U source priority ordering for stream sorting.

### v0.6.16 - Channel Loading Fix
Fixed channel loading issues with profile filtering.

### v0.6.15 - Smart Stream Sorting
Implemented quality-based stream sorting using stream_stats (resolution + FPS).

### v0.6.14 - CSV Headers Enhancement
Added comprehensive CSV headers with action name, execution mode, and settings.

### v0.6.13 - Channel Groups Filter Fix
Fixed Sort Alternate Streams ignoring channel groups filter setting.

### v0.6.12 - Sort Streams Fix
Critical fix for Sort Alternate Streams action using wrong API endpoint.

### v0.6.11 - Dry Run Mode & Sort Streams
Added dry run mode toggle, Sort Alternate Streams action, flexible scheduled task configuration.

### v0.6.10 - Lock Detection Enhancement
Added Stream model import, enhanced lock detection, manual lock clear action.

### v0.6.9 - IPTV Checker Integration
Filter dead streams (0x0 resolution) and optional scheduler coordination.

### v0.6.8 - Quality-Based Stream Ordering
Automatic quality-based stream ordering when assigning streams.

### v0.6.7 - Deduplication & Decade Fix
Stream deduplication, decade number preservation ("70s" not matching "90s"), plus sign handling.

### v0.6.3 - Numbered Channel Fix
Fixed false positive matches for numbered channels (Premier Sports 1 vs 2).

### v0.6.2 - Token Matching Fix
Fixed Sky Cinema channels matching incorrect streams.

### v0.6.0 - Major Refactor
Replaced Celery Beat with background threading scheduler, operation lock system, WebSocket notifications, centralized configuration.

---

## Upgrade Instructions

**For v0.7.4a**:
1. Replace `plugin.py` with v0.7.4a
2. Replace `fuzzy_matcher.py` with v26.018.0100
3. Restart Dispatcharr container
4. Re-run "Match & Assign Streams"

**IMPORTANT**: Both files must be updated together!

---

## Version Compatibility

| Plugin Version | Required fuzzy_matcher |
|---------------|------------------------|
| 0.7.4a | 26.018.0100+ |
| 0.7.3c | 25.358.0200+ |
| 0.7.4 | 25.356.0230+ |
| 0.7.3 | 25.354.1835+ |
| 0.7.2 | 25.354.1835+ |
