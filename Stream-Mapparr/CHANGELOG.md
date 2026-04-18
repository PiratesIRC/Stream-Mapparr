# Stream-Mapparr CHANGELOG

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
