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

## Background Operations

This plugin uses background threading to prevent browser timeouts during long operations.

- The frontend shows a green "Started in background" notification immediately — this does **not** mean the task is finished
- Buttons re-enable instantly. Do not click again; the task is running
- Monitor progress and completion in Docker logs:

```bash
docker logs -f dispatcharr | grep Stream-Mapparr
```

Look for `COMPLETED` or `CSV EXPORT CREATED` to know when the process is finished.

---

## Features

### Matching
- **Multi-stage fuzzy matching**: Exact, substring, and token-sort matching with configurable sensitivity (Relaxed/Normal/Strict/Exact)
- **US OTA callsign matching**: Dedicated action for matching US broadcast channels by callsign against authoritative database (5,900+ callsigns)
- **Multi-country channel databases**: US, UK, CA, AU, BR, DE, ES, FR, IN, MX, NL
- **Normalization cache**: Pre-normalizes stream names once for batch matching performance
- **rapidfuzz acceleration**: Uses C-accelerated Levenshtein when available (20-50x faster), with pure Python early-termination fallback

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

### Reporting
- **CSV exports**: Detailed reports with threshold recommendations and token mismatch analysis
- **Channel visibility management**: Auto-enable channels with streams, disable those without

## Requirements

- Dispatcharr v0.20.0+
- A Channel Profile (other than "All")

## Installation

1. Navigate to **Plugins** in Dispatcharr
2. Click **Import Plugin** and upload the plugin zip
3. Enable the plugin

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
- Threshold recommendations ("3 additional streams available at lower thresholds")
- Token mismatch analysis ("Add 'UK' to Ignore Tags")
- Match type breakdown (exact, substring, fuzzy)

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
