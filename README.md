# Stream-Mapparr
[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Stream-Mapparr)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Stream-Mapparr?include_prereleases&logo=github)](https://github.com/PiratesIRC/Stream-Mapparr/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Stream-Mapparr/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Stream-Mapparr/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Stream-Mapparr)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Stream-Mapparr)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Stream-Mapparr)
![License](https://img.shields.io/github/license/PiratesIRC/Stream-Mapparr)

A Dispatcharr plugin that automatically matches and assigns streams to channels using fuzzy matching, quality prioritization, and OTA callsign recognition.

## Backup Your Database

Before installing or using this plugin, create a backup of your Dispatcharr database. This plugin modifies channel and stream assignments.

**[Backup instructions](https://dispatcharr.github.io/Dispatcharr-Docs/user-guide/?h=backup#backup-restore)**

---

## Completion Notifications

Small jobs (estimated under ~25 seconds) run **synchronously** — the Dispatcharr UI shows the completion notification with the real result (channel/stream counts + CSV filename). Typical 18-channel match runs finish in ~15 seconds.

Larger jobs run in a **background thread**. The UI shows a "started in background" notification up front; completion is reported via:

- Docker logs: `docker logs -f dispatcharr | grep Stream-Mapparr` (look for `COMPLETED`)
- Optional **webhook** POST to an HTTP(S) endpoint of your choice (see settings)
- WebSocket event to the frontend

Buttons re-enable instantly. Do not click again while an operation is in flight — an operation lock prevents concurrent runs and auto-expires after 10 minutes.

---

## Features

### Matching
- **Multi-stage fuzzy matching**: Exact, substring, and token-sort matching with configurable sensitivity (Relaxed/Normal/Strict/Exact)
- **US OTA callsign matching**: Dedicated action for matching US broadcast channels by callsign against authoritative database (5,900+ callsigns)
- **Multi-country channel databases**: US, UK, CA, AU, BR, DE, ES, FR, IN, MX, NL
- **Zone-based channel variants**: East/West feeds for 33 major cable networks (FX, FXX, USA, Syfy, Disney Channel, etc.) via JSON `"zones"` array expansion
- **Country-restricted matching** (opt-in): Only match streams from the same detected country/group — e.g. `CANADA/CA` channels match only `CANADA/CA` streams
- **Normalization cache**: Pre-normalizes stream names once for batch matching performance
- **rapidfuzz acceleration**: Uses C-accelerated Levenshtein when available (20-50x faster), with pure Python early-termination fallback
- **Bulk ORM writes**: Stream assignments go through `bulk_create`, collapsing per-row DB round-trips to one query per channel

### Quality & Streams
- **Quality-based stream sorting**: 4K > UHD > FHD > HD > SD, using probed resolution (from IPTV Checker) or name-based detection
- **M3U source prioritization**: Prefer streams from specific M3U providers
- **Dead stream filtering**: Skip streams with 0x0 resolution (requires IPTV Checker)
- **Auto-deduplication**: Removes duplicate stream names during assignment

### Automation
- **Built-in scheduler**: Configure daily runs with timezone and HHMM time support
- **Rate limiting**: Configurable throttling (None/Low/Medium/High)
- **Operation lock**: Prevents concurrent tasks; auto-expires after 10 minutes
- **Dry run mode**: Preview results with CSV export without making changes
- **Webhook notifications**: POST JSON summary (action, counts, CSV filename, dry-run flag) to any HTTP(S) endpoint on completion — wire into Discord, Slack, Home Assistant, ntfy, etc.

### Reporting
- **CSV exports**: Detailed reports with threshold recommendations and token mismatch analysis
- **Channel visibility management**: Auto-enable channels with streams, disable those without

## Requirements

- Dispatcharr v0.20.0+
- A Channel Profile (other than "All")

## Installation

**From the Dispatcharr Plugin Hub (recommended):**

1. In Dispatcharr, go to **Settings → Plugin Hub**
2. Find **Stream-Mapparr** in the catalog and click **Install**
3. Enable the plugin

**Manual install:**

1. Download the latest zip from [Releases](https://github.com/PiratesIRC/Stream-Mapparr/releases)
2. In Dispatcharr, go to **Plugins → Import Plugin** and upload the zip
3. Enable the plugin

## Versioning

This plugin uses **calver** (`1.MAJOR.DDDHHMM`, UTC day-of-year + UTC time) — matching the Lineuparr / Channel-Mapparr / EPG-Janitor / IPTV Checker cohort. Run `python3 Stream-Mapparr/bump_version.py` to bump both `plugin.json` and `plugin.py` in sync.

## Settings

| Setting | Type | Default | Description |
|:---|:---|:---|:---|
| **Overwrite Existing Streams** | boolean | True | Replace existing streams vs append-only |
| **Match Sensitivity** | select | Normal (80) | Relaxed (70), Normal (80), Strict (90), Exact (95) |
| **Channel Profile** | select | - | Profile to process channels from (dropdown from DB) |
| **Channel Groups** | string | (all) | Specific groups to process, comma-separated |
| **Stream Groups** | string | (all) | Specific stream groups to use, comma-separated |
| **M3U Sources** | string | (all) | Specific M3U sources, comma-separated (order = priority) |
| **Prioritize Quality** | boolean | False | Sort by quality first, then M3U source priority |
| **Custom Ignore Tags** | string | (none) | Tags to strip before matching (e.g., `[Dead], (Backup)`) |
| **Tag Handling** | select | Strip All | Strip All / Keep Regional / Keep All |
| **Channel Database** | select | US | Channel database for callsign and name matching |
| **Visible Channel Limit** | number | 1 | Channels per group to enable and assign streams |
| **Rate Limiting** | select | None | None / Low / Medium / High |
| **Timezone** | select | US/Central | Timezone for scheduled runs |
| **Filter Dead Streams** | boolean | False | Skip 0x0 resolution streams (requires IPTV Checker) |
| **Restrict Matching To Same Country** | boolean | False | Only match streams whose detected country matches the channel's country/group |
| **Webhook URL** | string | (blank) | HTTP(S) endpoint to POST JSON summary on completion (see below) |
| **Fire Webhook On Completion** | boolean | False | Enable webhook delivery for Match & Assign / Match OTA / Sort Streams |
| **Scheduled Run Times** | string | (none) | HHMM times, comma-separated (e.g., `0400,1600`) |
| **Dry Run Mode** | boolean | False | Preview without making database changes |

## Actions

| Action | Description |
|:---|:---|
| **Validate Settings** | Check configuration, profiles, groups, databases |
| **Load/Process Channels** | Load channel and stream data from database |
| **Preview Changes** | Dry-run with CSV export |
| **Match & Assign Streams** | Fuzzy match and assign streams to channels |
| **Match US OTA Only** | Match US broadcast channels by callsign |
| **Sort Alternate Streams** | Re-sort existing streams by quality |
| **Manage Channel Visibility** | Enable/disable channels based on stream count |
| **Clear CSV Exports** | Delete all plugin CSV files |

## Scheduling

1. Set **Timezone** (e.g., `US/Central`)
2. Set **Scheduled Run Times** in 24-hour format (e.g., `0400,1600` for 4 AM and 4 PM)
3. Enable **CSV Export** if desired
4. Click **Update Schedule**

The scheduler runs in a background thread and restarts automatically with the container.

## CSV Reports

Preview and scheduled exports are saved to `/data/exports/`. Reports include:
- Threshold recommendations ("3 additional streams available at lower thresholds") — shown by **Preview Changes**
- Token mismatch analysis ("Add 'UK' to Ignore Tags") — shown by **Preview Changes**
- Per-channel match counts (shown by **Match & Assign Streams**)
- Match type breakdown (exact, substring, fuzzy)

## Webhooks

Set **Webhook URL** to any HTTP(S) endpoint and enable **Fire Webhook On Completion**. On each `add_streams_to_channels`, `match_us_ota_only`, or `sort_streams` completion, the plugin POSTs a JSON payload in a daemon thread (fire-and-forget, does not block the action):

```json
{
  "plugin": "stream-mapparr",
  "event": "add_streams_to_channels.complete",
  "action": "add_streams_to_channels",
  "status": "success",
  "message": "Matched and assigned 82 streams across 18 channels. Report: ...csv",
  "timestamp": "2026-04-18T21:34:23Z",
  "dry_run": false,
  "channels_updated": 18,
  "streams_assigned": 82,
  "channels_skipped": 0,
  "csv": "stream_mapparr_20260418_213422.csv"
}
```

The URL is fetched server-side from Dispatcharr — only enter endpoints you trust.

## Troubleshooting

**Operation seems stuck?**
Check Docker logs. If another operation is running, wait for completion (lock auto-expires after 10 min) or click **Clear Operation Lock**.

**No matches found?**
- Lower Match Sensitivity from Strict to Normal or Relaxed
- For US OTA channels, use **Match US OTA Only** instead of fuzzy matching
- Check that the correct Channel Database is selected

**System slow during scanning?**
Set Rate Limiting to Medium or High.

```bash
# Monitor plugin activity
docker logs -f dispatcharr | grep Stream-Mapparr

# Check CSV exports
docker exec dispatcharr ls -lh /data/exports/

# Check plugin files
docker exec dispatcharr ls -la /data/plugins/stream-mapparr/
```

## Changelog

See [CHANGELOG.md](Stream-Mapparr/CHANGELOG.md) for full version history.

## License

MIT
