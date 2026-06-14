# Stream-Mapparr CHANGELOG

## Unreleased

_Nothing yet._

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
