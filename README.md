# Stream Mapparr
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Stream-Mapparr)

A Dispatcharr plugin that automatically matches and assigns streams to channels based on advanced fuzzy matching, quality prioritization, and OTA callsign recognition.

## Recommendation
It is strongly recommended to use the **[Channel Maparr Plugin](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)** before using this plugin. Running Channel Maparr first to standardize your channel names will significantly improve matching accuracy.

## ⚠️ Important: Backup Your Database
Before installing or using this plugin, it is **highly recommended** that you create a backup of your Dispatcharr database. This plugin makes significant changes to your channel and stream assignments.

**[Click here for instructions on how to back up your database.](https://dispatcharr.github.io/Dispatcharr-Docs/troubleshooting/?h=backup#how-can-i-make-a-backup-of-the-database)**

## Features
* **Advanced Fuzzy Matching**: Automatically finds and assigns streams to channels using an advanced fuzzy-matching engine (`fuzzy_matcher.py`).
* **Unlimited Stream Support**: Fetches and processes ALL available streams regardless of quantity (no 10,000 stream limit).
* **Enhanced OTA Callsign Matching**: Uses a robust `*_channels.json` database for superior callsign extraction and matching for Over-The-Air broadcast channels.
* **Multi-Stream Assignment**: Assigns **all** matching streams to each channel (e.g., 4K, FHD, HD versions), sorted by quality.
* **Quality Prioritization**: Sorts matched streams by quality (4K → FHD → HD → (H) → (F) → (D) → SD → Slow).
* **Channel Visibility Management**: Automatically enables/disables channels based on stream assignments and duplicate detection.
* **Channel Database Integration**: Uses `*_channels.json` files for robust OTA and cable channel identification.
* **Customizable Ignore Tags**: Filter out unwanted tags during matching.
* **Profile & Group Filtering**: Target specific channel profiles and groups.
* **Preview Mode**: Dry-run CSV export to review matches before applying changes.
* **CSV Export Management**: Built-in cleanup tool to delete old export files.
* **Real-time Updates**: WebSocket-based frontend refresh for immediate visual feedback.
* **Comprehensive Logging**: Detailed matching logic and debug information.

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access
* **A Channels Profile other than "All"** ([Profile Documentation](https://dispatcharr.github.io/Dispatcharr-Docs/user-guide/#channels_1))
* Multiple streams of the same channel available in your setup
* `fuzzy_matcher.py` (matching engine) - included
* `*_channels.json` files (channel database) - included

## Installation
1.  Log in to Dispatcharr's web UI
2.  Navigate to **Plugins**
3.  Click **Import Plugin** and upload the plugin zip file
4.  Enable the plugin after installation

## Settings Reference

| Setting | Type | Default | Description |
|:---|:---|:---|:---|
| **Fuzzy Match Threshold** | `number` | 85 | Minimum similarity score (0-100) for fuzzy matching. Higher values require closer matches. |
| **Dispatcharr URL** | `string` | - | Full URL of your Dispatcharr instance (e.g., `http://192.168.1.10:9191`) |
| **Dispatcharr Admin Username** | `string` | - | Username for API authentication |
| **Dispatcharr Admin Password** | `password` | - | Password for API authentication |
| **Profile Name** | `string` | - | Name of an existing Channel Profile to process (e.g., "Primary", "Sports") |
| **Channel Groups** | `string` | - | Comma-separated group names to process, or leave empty for all groups |
| **Ignore Tags** | `string` | - | Comma-separated tags to ignore during matching (e.g., `4K, [4K], [Dead]`) |
| **Visible Channel Limit** | `number` | 1 | Number of channels per matching group that will be visible and have streams added |

## Usage Guide

### Step-by-Step Workflow
1.  **Configure Settings**
    * Enter your Dispatcharr URL, username, and password
    * Adjust the **Fuzzy Match Threshold** slider (85 is a good default)
    * Specify an existing **Profile Name** (cannot be "All")
    * Optionally specify **Channel Groups** (leave empty to process all)
    * Optionally configure **Ignore Tags**
    * Set **Visible Channel Limit** (default: 1 channel per matching group)
    * Click **Save Settings**

2.  **Load and Process Channels**
    * Click **Run** on `Load/Process Channels`
    * The plugin loads channels from the specified profile and groups
    * Fetches all available streams (handles pagination automatically with no limit)
    * Review the summary showing channel and stream counts

3.  **Preview Changes (Dry Run)**
    * Click **Run** on `Preview Changes (Dry Run)`
    * Exports a CSV to `/data/exports/stream_mapparr_preview_YYYYMMDD_HHMMSS.csv`
    * Review matched streams for each channel
    * Shows which channels will be updated vs skipped
    * Identifies channels without matches

4.  **Add Streams to Channels**
    * Click **Run** on `Add Stream(s) to Channels`
    * Adds **all** matched streams to each channel (sorted by quality)
    * Only updates channels within the visible channel limit
    * Enables updated channels in the profile
    * Triggers real-time frontend refresh via WebSocket
    * Exports results to `/data/exports/stream_mapparr_update_YYYYMMDD_HHMMSS.csv`

5.  **Manage Channel Visibility (Optional)**
    * Click **Run** on `Manage Channel Visibility`
    * Disables all channels in the profile
    * Enables only the single highest-priority, eligible channel from each matching group
    * Useful for cleaning up duplicate channels
    * Triggers real-time frontend refresh via WebSocket
    * Exports visibility report to `/data/exports/stream_mapparr_visibility_YYYYMMDD_HHMMSS.csv`

6.  **Clear CSV Exports (Optional)**
    * Click **Run** on `Clear CSV Exports`
    * Deletes all CSV files created by this plugin from `/data/exports/`
    * Shows list of deleted files (up to 10 listed with count of additional files)
    * Useful for cleaning up old reports and freeing disk space

## Matching Logic
Stream-Mapparr v0.4 uses a new `FuzzyMatcher` engine and a channel database for all matching.

1.  **Channel Grouping**: Channels are grouped by a 'group key'.
    * **OTA Channels**: Grouped by callsign (e.g., `OTA_WSBT`).
    * **Standard Channels**: Grouped by their cleaned name (e.g., `HBO`).

2.  **Channel Prioritization**: Within each group, channels are sorted by quality tags (e.g., `[4K]` > `[HD]`) and then by channel number (ascending).

3.  **Matching**: The plugin uses the **highest priority** channel from the group (e.g., `CBS - IN South Bend (WSBT) [4K]` or `HBO [4K]`) as the "search query".
    * **OTA Matching**: If the channel is OTA, the `FuzzyMatcher` looks up its callsign (`WSBT`) in the `*_channels.json` database and searches all streams for that callsign.
    * **Standard Matching**: If not OTA, the `FuzzyMatcher` cleans the channel name (e.g., to `HBO`) and uses advanced fuzzy matching (token sorting, similarity ratios) to find the best match from the stream list (e.g., matching `HBO` to `HBO US`).

4.  **Multi-Stream Collection**: Once a match is found (e.g., stream name "HBO US"), the plugin gathers **all** quality variations of that matched stream (e.g., `HBO US [4K]`, `HBO US [HD]`, `HBO US [SD]`).

5.  **Assignment**: These collected streams are sorted by quality and assigned to the high-priority channel(s) in the group, up to the **Visible Channel Limit**.

### Channel Grouping and Priority
Channels are grouped by callsign (OTA) or cleaned name (regular):
* Within each group, channels are sorted by quality tag priority:
    * `[4K]` → `[FHD]` → `[HD]` → `[SD]` → `[Unknown]` → `[Slow]` → No tag
* Then by channel number (ascending)
* Only the highest priority channels (up to Visible Channel Limit) receive streams

### Quality Sorting
Matched streams are automatically sorted by quality precedence:
* **4K**: `[4K]`, `(4K)`, `4K`
* **FHD**: `[FHD]`, `(FHD)`, `FHD`
* **HD**: `[HD]`, `(HD)`, `HD`, `(H)`
* **SD**: `[SD]`, `(SD)`, `SD`
* **Other**: `(F)`, `(D)`
* **Slow**: `Slow`, `[Slow]`, `(Slow)`

### Duplicate Channel Handling
**Channels in Profile**:
* `CBS - AL Birmingham (WIAT) [FHD]` ← Highest priority (enabled, receives streams)
* `CBS - AL Birmingham (WIAT) [HD]` ← Lower priority (skipped)
* `CBS - AL Birmingham (WIAT) [Slow] [HD]` ← Lower priority (skipped)

**Result**: With `Visible Channel Limit` set to 1, only the `[FHD]` channel receives streams and is visible.

## Action Reference

| Action | Description |
|:---|:---|
| **Load/Process Channels** | Load channels from specified profile/groups and fetch all available streams (unlimited) |
| **Preview Changes (Dry Run)** | Export CSV showing which streams will be matched to each channel |
| **Add Stream(s) to Channels** | Apply **all** matched streams to channels, enabling highest priority channels in each group |
| **Manage Channel Visibility** | Disable all channels, then enable only the single highest-priority, eligible channel from each matching group |
| **Clear CSV Exports** | Delete all CSV export files created by this plugin to free up disk space |

## File Locations
* **Processing Cache**: `/data/stream_mapparr_processed.json`
* **Preview Export**: `/data/exports/stream_mapparr_preview_YYYYMMDD_HHMMSS.csv`
* **Results Report**: `/data/exports/stream_mapparr_update_YYYYMMDD_HHMMSS.csv`
* **Visibility Report**: `/data/exports/stream_mapparr_visibility_YYYYMMDD_HHMMSS.csv`
* **Plugin Files**:
    * `/data/plugins/stream_mapparr/plugin.py`
    * `/data/plugins/stream_mapparr/__init__.py`
    * `/data/plugins/stream_mapparr/fuzzy_matcher.py` (Matching Engine)
    * `/data/plugins/stream_mapparr/*_channels.json` (Channel Database)

## CSV Export Format

### Preview CSV (`stream_mapparr_preview_...csv`)
| Column | Description |
|:---|:---|
| **will_update** | `Yes` if channel will be updated, `No` if skipped |
| **channel_id** | Internal Dispatcharr channel ID |
| **channel_name** | Channel name |
| **channel_name_cleaned** | The cleaned name used for matching |
| **channel_number** | Channel number |
| **matched_streams** | Number of streams matched |
| **match_reason** | How the match was found (e.g., `Fuzzy match (exact, score: 100)`) |
| **stream_names** | Semicolon-separated list of matched stream names |

### Results CSV (`stream_mapparr_update_...csv`)
| Column | Description |
|:---|:---|
| **channel_name** | Channel name that was updated |
| **stream_names** | Semicolon-separated list of added stream names |
| **matched_streams** | Number of streams added |

### Visibility Report CSV (`stream_mapparr_visibility_...csv`)
| Column | Description |
|:---|:---|
| **channel_id** | Internal Dispatcharr channel ID |
| **channel_name** | Channel name |
| **stream_count** | Number of streams attached to channel |
| **reason** | Why channel was enabled/disabled (e.g., `2+ streams`, `Duplicate`, `Attached`) |
| **enabled** | `Yes` if channel is visible, `No` if hidden |

## Troubleshooting

### Common Issues

**"Profile Name must be configured"**
* Enter an existing profile name in the settings
* Profile name is case-sensitive
* Cannot use "All" - must create a separate profile

**"None of the specified groups were found"**
* Verify group names are spelled correctly (case-sensitive)
* Check available groups in the error message
* Leave field empty to process all groups

**OTA channels not matching streams**
* Verify channel name format: `NETWORK - STATE City (CALLSIGN)`
* Example: `CBS - IN South Bend (WSBT) [HD]`
* Check that callsign exists in stream names
* Review logs for `FuzzyMatcher` and `callsign` messages
* Ensure `*_channels.json` files are present in the plugin directory

**Too many duplicate channels visible**
* Run "Manage Channel Visibility" action
* Adjust "Visible Channel Limit" setting (default: 1)
* Only highest priority channels per matching group will remain visible

**Channels not matching streams**
* Run Channel Mapparr first to standardize channel names
* Adjust the **"Fuzzy Match Threshold"** slider (try a lower value like 80)
* Check that streams exist for your channels
* Use Preview mode to see matching details in logs

**Too many false positives**
* Increase the **"Fuzzy Match Threshold"** slider (try 90 or 95)
* Use Ignore Tags to filter out common unwanted words
* Check logs for matching logic details

**Frontend not updating after changes**
* Plugin uses WebSocket-based updates for real-time refresh
* If changes are not immediately visible, refresh your browser
* Check logs for "Frontend refresh triggered via WebSocket" message

**Many old CSV export files**
* Use the "Clear CSV Exports" action to delete all old reports
* This will free up disk space in `/data/exports/`

**"Plugin 'stream-mapparr' not found" error**
* Log out of Dispatcharr
* Refresh your browser or close and reopen it
* Log back in and try again

### Updating the Plugin

To update Stream-Mapparr from a previous version:

1.  **Remove Old Version**
    * Navigate to **Plugins** in Dispatcharr
    * Click the trash icon next to the old Stream-Mapparr plugin
    * Confirm deletion

2.  **Restart Dispatcharr**
    * Log out of Dispatcharr
    * Restart the Docker container:
        ```bash
        docker restart dispatcharr
        ```

3.  **Install New Version**
    * Log back into Dispatcharr
    * Navigate to **Plugins**
    * Click **Import Plugin** and upload the new plugin zip file
    * Enable the plugin after installation

4.  **Verify Installation**
    * Check that the new version number appears in the plugin list
    * Reconfigure your settings if needed
    * Run "Load/Process Channels" to test

## FAQ

### How do I improve matching accuracy?

**Option 1: Use Channel Mapparr Plugin (Recommended)**
* Install and run the [Channel Mapparr Plugin](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin)
* This automatically standardizes channel names across your entire setup
* Provides consistent naming that Stream-Mapparr can reliably match

**Option 2: Adjust Fuzzy Match Threshold**
* Navigate to the plugin settings
* If you are missing matches, try a **lower** value (e.g., `80`).
* If you are getting bad/incorrect matches, try a **higher** value (e.g., `90` or `95`).

### How many streams can the plugin handle?

Stream-Mapparr has no stream limit. The plugin automatically handles pagination and will fetch all available streams regardless of quantity. It has been successfully tested with libraries containing 10,000+ streams.

### Why use Visible Channel Limit?

The Visible Channel Limit setting controls how many channels per group receive streams and remain visible. For example, if you have:
* `CBS - AL Birmingham (WIAT) [FHD]`
* `CBS - AL Birmingham (WIAT) [HD]`
* `CBS - AL Birmingham (WIAT) [SD]`

With a limit of 1, only the highest priority channel (`[FHD]`) receives streams and stays visible. This prevents duplicate channels from cluttering your guide.

### What happens to existing stream assignments?

When you run "Add Stream(s) to Channels", the plugin:
* Removes **all** existing stream assignments from matched channels
* Adds **all** the new matched streams (sorted by quality)
* Only affects channels within the Visible Channel Limit

Channels outside the limit or without matches are left unchanged.

### Can I undo changes?

The plugin exports CSV reports for every action:
* Preview reports show what will change
* Results reports show what was changed
* Visibility reports show which channels were enabled/disabled

You can use these reports to manually revert changes if needed. However, there is no built-in "undo" function.

### How do I clean up old export files?

Use the "Clear CSV Exports" action to delete all CSV files created by Stream-Mapparr. This removes files from `/data/exports/` that start with `stream_mapparr_` and helps free up disk space.

### Debugging Commands
```bash
# Check plugin files
docker exec dispatcharr ls -la /data/plugins/stream_mapparr/

# Monitor plugin activity
docker logs -f dispatcharr | grep -i stream_mapparr

# View processing cache
docker exec dispatcharr cat /data/stream_mapparr_processed.json | jq

# Check fuzzy matching activity
docker logs -f dispatcharr | grep -i "FuzzyMatcher"

# Check match type and score
docker logs -f dispatcharr | grep "match_type"

# Check CSV exports
docker exec dispatcharr ls -lh /data/exports/stream_mapparr_*
