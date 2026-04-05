# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Stream-Mapparr is a Python plugin (v0.9.0) for the Dispatcharr application that automatically matches IPTV streams to TV channels using fuzzy name matching and quality precedence. The plugin uses Django ORM for direct database access and manages channel-stream assignments across multiple channel profiles and groups.

## Architecture

### Core Components

**plugin.py** - Main plugin implementation containing:
- `Plugin` class: Dispatcharr plugin interface with dynamic `fields` property, action handlers, and settings resolvers
- `SmartRateLimiter`: Rate limiting for pacing ORM writes
- `ProgressTracker`: Progress logging with ETA estimation
- Background threading with `_try_start_thread` lock for long-running operations
- Multi-stage fuzzy matching pipeline with quality-based stream sorting
- Settings resolvers: `_resolve_match_threshold`, `_resolve_ignore_flags`, `_resolve_enabled_databases`

**fuzzy_matcher.py** (v26.095.0100) - Fuzzy matching library:
- `rapidfuzz` C-accelerated Levenshtein when available (20-50x faster), pure Python early-termination fallback
- Normalization cache: `precompute_normalizations()`, `_get_cached_norm()`, `_get_cached_processed()`
- Multi-stage matching: exact → substring → token-sort fuzzy
- `calculate_similarity()` with optional `threshold` parameter for early termination
- Categorized tag normalization (quality/regional/geographic/misc)
- OTA broadcast channel callsign extraction and matching
- Channel database loading from `*_channels.json` files

**Channel Databases** - Country-specific JSON files (`US_channels.json`, `UK_channels.json`, etc.):
- Structure: `{"country_code": "XX", "country_name": "Name", "version": "1.0", "channels": [...]}`
- Channel types: "broadcast (OTA)" (with callsigns) or "premium/cable/national"
- Selected via single `channel_database` dropdown (None / country code / All)

### Data Flow

1. **Settings Validation** → Profile/group resolution via ORM
2. **Channel Loading** → Fetch channels via Django ORM filtered by profile(s) and groups
3. **Stream Loading** → Fetch all streams via ORM, optionally filtered by M3U source
4. **Pre-normalization** → `precompute_normalizations()` caches all stream name normalizations
5. **Grouping** → Channels grouped by normalized name (OTA channels grouped by callsign)
6. **Matching** → For each group, match streams using fuzzy matcher with cached lookups
7. **Prioritization** → Within each group, channels sorted by quality tag then channel number
8. **Assignment** → Top N channels per group get matched streams (configurable via `visible_channel_limit`)

### ORM Integration (v0.8.0+)

The plugin uses Django ORM directly (no HTTP API):
- Models: `Channel`, `ChannelGroup`, `ChannelProfile`, `ChannelProfileMembership`, `ChannelStream`, `Stream`
- ORM helpers: `_get_all_profiles`, `_get_all_groups`, `_get_all_channels`, `_get_all_streams`, `_get_all_m3u_accounts`, `_get_stream_groups`
- All helpers return dicts via `.values()` for downstream compatibility
- Frontend refresh: `send_websocket_update('updates', 'update', {...})`

### Scheduling

Uses background threading (`_start_background_scheduler` / `_stop_background_scheduler`), NOT Celery Beat:
- `scheduled_times` setting: comma-separated HH:MM times
- `update_schedule_action` is the entry point for schedule changes
- Timezone-aware via `pytz`

## Development

### Testing Changes

No automated test suite. Manual testing workflow:
1. Modify plugin code in `Stream-Mapparr/` directory
2. Deploy to container: `docker cp Stream-Mapparr/plugin.py dispatcharr:/data/plugins/stream-mapparr/`
3. Restart: `cd /path/to/dispatcharr && docker compose restart`
4. Run actions via Dispatcharr plugin UI
5. Monitor: `docker logs -f dispatcharr | grep Stream-Mapparr`
6. Always verify syntax first: `python -m py_compile Stream-Mapparr/plugin.py`

### Key Settings (v0.9.0)

| Setting ID | Type | Default | Description |
|---|---|---|---|
| `match_sensitivity` | select | normal | Relaxed (70) / Normal (80) / Strict (90) / Exact (95) |
| `profile_name` | select | (none) | Dynamic dropdown from ChannelProfile table |
| `tag_handling` | select | strip_all | Strip All / Keep Regional / Keep All |
| `channel_database` | select | US | None / country code / All |
| `visible_channel_limit` | number | 1 | Channels per group to enable |
| `overwrite_streams` | boolean | true | Replace existing streams vs append |
| `rate_limiting` | select | none | None / Low / Medium / High |
| `prioritize_quality` | boolean | false | Quality before M3U source priority |

Legacy field IDs (`fuzzy_match_threshold`, `ignore_quality_tags`, `ignore_regional_tags`, `ignore_geographic_tags`, `ignore_misc_tags`, `db_enabled_{XX}`) still work via resolver fallbacks.

### Performance

- `rapidfuzz` C extension provides 20-50x Levenshtein speedup when installed
- `precompute_normalizations()` called before matching loops eliminates redundant `normalize_name()` calls
- `calculate_similarity(threshold=...)` enables early termination in both rapidfuzz and pure Python paths
- `ESTIMATED_SECONDS_PER_ITEM = 0.1` (with rapidfuzz; was 7.73 before optimization)

### Adding Channel Databases

Create `{COUNTRY_CODE}_channels.json` files with structure:
```json
{
  "country_code": "XX",
  "country_name": "Country Name",
  "version": "1.0",
  "channels": [
    {"channel_name": "Channel Name", "type": "premium/cable/national", "category": "News"},
    {"channel_name": "WABC New York", "type": "broadcast (OTA)", "callsign": "WABC-TV", "category": "General"}
  ]
}
```

The plugin auto-detects `*_channels.json` files and populates the Channel Database dropdown.

### Persistence

- `/data/stream_mapparr_processed.json`: Loaded channel/stream data and settings snapshot
- `/data/stream_mapparr_version_check.json`: Cached GitHub version check (24-hour TTL)
- `/data/stream_mapparr_operation.lock`: File-based operation lock (10-min auto-expire)
- `/data/exports/`: CSV export files
