# Stream Mapparr
A Dispatcharr plugin that automatically matches and assigns streams to channels based on intelligent name matching, quality prioritization, and timezone filtering.

## Features
* **Intelligent Stream Matching**: Automatically finds and assigns streams to channels using sophisticated name-matching algorithms
* **OTA Callsign Matching**: Direct callsign extraction and matching for Over-The-Air broadcast channels
* **Multiple Streams Per Channel**: Assigns all matching streams to each channel, sorted by quality
* **Quality Prioritization**: Sorts matched streams by quality (4K → FHD → HD → (H) → (F) → (D) → SD → Slow)
* **Channel Visibility Management**: Automatically enables/disables channels based on stream assignments and duplicate detection
* **Timezone Filtering**: Automatically excludes West/Pacific coast feeds unless explicitly requested
* **Networks.json Integration**: FCC broadcast station database for OTA channel validation
* **Channels.txt Integration**: Distinguishes between similar channel names (e.g., FX vs FXX vs FX Movie Channel)
* **Customizable Ignore Tags**: Filter out unwanted tags during matching
* **Profile & Group Filtering**: Target specific channel profiles and groups
* **Preview Mode**: Dry-run CSV export to review matches before applying changes
* **Comprehensive Logging**: Detailed matching logic and debug information

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access
* **A Channels Profile other than "All"** ([Profile Documentation](https://dispatcharr.github.io/Dispatcharr-Docs/user-guide/#channels_1))
* Multiple streams of the same channel available in your setup
* `channels.txt` file (curated channel list) - included
* `networks.json` file (FCC broadcast station database) - included for OTA support

## Recommendation
It is recommended to use the [Channel Mapparr Plugin](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin) before using this plugin to standardize channel names. Standardized channel names significantly improve matching accuracy.

## Installation
1. Log in to Dispatcharr's web UI
2. Navigate to **Plugins**
3. Click **Import Plugin** and upload the plugin zip file
4. Enable the plugin after installation

## Settings Reference

| Setting | Type | Default | Description |
|:--------|:-----|:--------|:------------|
| **Dispatcharr URL** | `string` | - | Full URL of your Dispatcharr instance (e.g., `http://192.168.1.10:9191`) |
| **Dispatcharr Admin Username** | `string` | - | Username for API authentication |
| **Dispatcharr Admin Password** | `password` | - | Password for API authentication |
| **Profile Name** | `string` | - | Name of an existing Channel Profile to process (e.g., "Primary", "Sports") |
| **Channel Groups** | `string` | - | Comma-separated group names to process, or leave empty for all groups |
| **Ignore Tags** | `string` | - | Comma-separated tags to ignore during matching (e.g., `4K, [4K], [Dead]`) |
| **Visible Channel Limit** | `number` | 1 | Number of channels per callsign group that will be visible and have streams added |

## Usage Guide

### Step-by-Step Workflow
1. **Configure Settings**
   * Enter your Dispatcharr URL, username, and password
   * Specify an existing **Profile Name** (cannot be "All")
   * Optionally specify **Channel Groups** (leave empty to process all)
   * Optionally configure **Ignore Tags** to filter out unwanted tags
   * Set **Visible Channel Limit** (default: 1 channel per callsign group)
   * Click **Save Settings**

2. **Load and Process Channels**
   * Click **Run** on `Load/Process Channels`
   * The plugin loads channels from the specified profile and groups
   * Fetches all available streams (handles pagination automatically)
   * Review the summary showing channel and stream counts

3. **Preview Changes (Dry Run)**
   * Click **Run** on `Preview Changes (Dry Run)`
   * Exports a CSV to `/data/exports/stream_mapparr_preview_YYYYMMDD_HHMMSS.csv`
   * Review matched streams for each channel
   * Shows which channels will be updated vs skipped
   * Identifies channels without matches

4. **Add Streams to Channels**
   * Click **Run** on `Add Stream(s) to Channels`
   * Adds all matched streams to each channel (sorted by quality)
   * Only updates channels within the visible channel limit
   * Enables updated channels in the profile
   * Triggers M3U refresh to update the GUI
   * Exports results to `/data/exports/stream_mapparr_results_YYYYMMDD_HHMMSS.csv`

5. **Manage Channel Visibility (Optional)**
   * Click **Run** on `Manage Channel Visibility`
   * Disables all channels in the profile
   * Enables only channels with 0-1 streams (not attached to other channels)
   * Enables only the highest priority channel per callsign group
   * Useful for cleaning up duplicate channels
   * Exports visibility report to `/data/exports/stream_mapparr_visibility_YYYYMMDD_HHMMSS.csv`

## Matching Logic

### OTA Channel Matching (Priority Method)
For broadcast channels like "CBS - IN South Bend (WSBT) [HD]":

1. **Callsign Extraction**: Parses channel name BEFORE any cleaning to extract callsign
   * Pattern: `NETWORK - STATE City (CALLSIGN) [Quality]`
   * Example: `CBS - IN South Bend (WSBT) [HD]` → Callsign: `WSBT`

2. **Direct Callsign Matching**: Searches all streams for the exact callsign using word boundaries
   * Matches: `US CBS 22 (WSBT) South Bend/Elkhart Area (H)`
   * Matches: `CBS: IN SOUTH BEND WSBT`
   * Excludes: `WSBTV` (different callsign)

3. **Fallback to Networks.json**: If direct matching fails, validates against FCC database
   * Confirms network affiliation
   * Validates state and callsign combination
   * Searches streams for validated callsign

4. **Quality Sorting**: All matched streams sorted by quality precedence

### Standard Channel Matching
For non-OTA channels, the plugin uses a multi-stage process:

1. **Exact Match After Cleaning**: Matches channel names after removing quality tags
   * Example: "TBS [FHD]" → "TBS" matches "US: TBS"

2. **Word Boundary Matching**: Ensures complete word matches to avoid false positives
   * Example: "FX" matches "US: FX" but NOT "FXX" or "WFXR"

3. **Longer Channel Filtering**: Uses `channels.txt` to distinguish similar names
   * Example: "FX" does NOT match "FX Movie Channel" (different channel)

4. **Call Sign Filtering**: Excludes local TV station call signs
   * Example: "FX" does NOT match "WBKB FX (WBKBDT4)" (local FOX affiliate)

5. **Timezone Filtering**: Automatically excludes West/Pacific feeds
   * Example: "SYFY [HD]" matches "US NBC SYFY (East)" but NOT "US NBC SYFY (West)"

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

## Example Transformations

### OTA Channels (Callsign Matching)
**Channel**: `CBS - IN South Bend (WSBT) [HD]`  
**Matched Streams**: `US CBS 22 (WSBT) South Bend/Elkhart Area (H)`, `US CBS 22 (WSBT) South Bend/Elkhart Area (F)`, `CBS: IN SOUTH BEND WSBT`

**Channel**: `CBS - CA Monterey (KION) [FHD]`  
**Matched Streams**: `US CBS 46 (KION) Monterey (H)`, `US CBS 46 (KION) Monterey (F)`, `CBS: CA MONTEREY KION`, `US: FOX Monterey (KION)`

**Channel**: `CBS - NM Albuquerque (KRQE) [HD]`  
**Matched Streams**: `US FOX (KRQE) Albuquerque Santa Fe (H)`, `US CBS 13 (KRQE) Albuquerque Santa Fe (H)`, `US FOX (KRQE) Alburquerque (F)`, `US CBS (KRQE) Albuquerque (F)`, `CBS: NM ALBUQUERQUE KRQE`, `FOX: ALBUQUERQUE NM KRQE`, `US: CBS 13 Albuquerque (KRQE)`, `US: FOX 13 Albuquerque (KRQE)`

### Standard Channels
**Channel**: `TBS [FHD]`  
**Matched Streams**: `USA: TBS`, `US: TBS`, `US: TBS`

**Channel**: `FX (East) [HD]`  
**Matched Streams**: `US: FX`, `US: FX`, `US FX (East) (S)`, `US FX (East) (H)`, `US FX (East) (A)`, `US (F2) FX (East)`  
**Excluded**: `US: FX Movie Channel`, `(US) (FOX1) WBKB FX (WBKBDT4)`

**Channel**: `SYFY [HD]`  
**Matched Streams**: `USA SYFY`, `US: SYFY`, `US: SYFY`, `US NBC SYFY (East) (D)`  
**Excluded**: `US NBC SYFY (West) (D)`

### Duplicate Channel Handling
**Channels in Profile**:
* `CBS - AL Birmingham (WIAT) [FHD]` ← Highest priority (enabled, receives streams)
* `CBS - AL Birmingham (WIAT) [HD]` ← Lower priority (skipped)
* `CBS - AL Birmingham (WIAT) [Slow] [HD]` ← Lower priority (skipped)

**Result**: Only the `[FHD]` channel receives streams and is visible

## Action Reference

| Action | Description |
|:-------|:------------|
| **Load/Process Channels** | Load channels from specified profile/groups and fetch all available streams |
| **Preview Changes (Dry Run)** | Export CSV showing which streams will be matched to each channel |
| **Add Stream(s) to Channels** | Apply matched streams to channels, enabling highest priority channels in each group |
| **Manage Channel Visibility** | Disable all channels, then enable only channels with 0-1 streams (excluding duplicates and attached channels) |

## File Locations
* **Processing Cache**: `/data/stream_mapparr_processed.json`
* **Preview Export**: `/data/exports/stream_mapparr_preview_YYYYMMDD_HHMMSS.csv`
* **Results Report**: `/data/exports/stream_mapparr_results_YYYYMMDD_HHMMSS.csv`
* **Visibility Report**: `/data/exports/stream_mapparr_visibility_YYYYMMDD_HHMMSS.csv`
* **Plugin Files**:
  * `/data/plugins/stream_mapparr/plugin.py`
  * `/data/plugins/stream_mapparr/__init__.py`
  * `/data/plugins/stream_mapparr/channels.txt`
  * `/data/plugins/stream_mapparr/networks.json`

## CSV Export Format

### Preview CSV
| Column | Description |
|:-------|:------------|
| **channel_id** | Internal Dispatcharr channel ID |
| **channel_name** | Channel name |
| **channel_number** | Channel number |
| **matched_streams_count** | Number of streams matched |
| **stream_names** | Pipe-separated list of matched stream names |
| **stream_ids** | Comma-separated list of matched stream IDs |
| **will_update** | `Yes` if channel will be updated, `No` if skipped |

### Results CSV
| Column | Description |
|:-------|:------------|
| **channel_id** | Internal Dispatcharr channel ID |
| **channel_name** | Channel name |
| **channel_number** | Channel number |
| **matched_streams_count** | Number of streams added |
| **stream_names** | Pipe-separated list of added stream names |
| **stream_ids** | Comma-separated list of added stream IDs |
| **will_update** | `Yes` if channel was updated, `No` if skipped |
| **status** | `Updated`, `Skipped (over limit)`, `No matches`, or error details |

### Visibility Report CSV
| Column | Description |
|:-------|:------------|
| **channel_id** | Internal Dispatcharr channel ID |
| **channel_name** | Channel name |
| **stream_count** | Number of streams attached to channel |
| **reason** | Why channel was enabled/disabled |
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
* Review logs for "Extracted callsign" and "Found callsign match" messages
* Ensure `networks.json` contains the station data (fallback method)

**Too many duplicate channels visible**
* Run "Manage Channel Visibility" action
* Adjust "Visible Channel Limit" setting (default: 1)
* Only highest priority channels per callsign group will remain visible

**Channels not matching streams**
* Run Channel Mapparr first to standardize channel names
* Check that streams exist for your channels
* Use Preview mode to see matching details in logs
* Verify `channels.txt` contains your channel names

**Too many false positives**
* Add entries to `channels.txt` to distinguish similar names
* Use Ignore Tags to filter out quality indicators
* Check logs for matching logic details

### Debugging Commands
```bash
# Check plugin files
docker exec dispatcharr ls -la /data/plugins/stream_mapparr/

# Monitor plugin activity
docker logs -f dispatcharr | grep -i stream_mapparr

# View processing cache
docker exec dispatcharr cat /data/stream_mapparr_processed.json | jq

# Check OTA callsign extraction
docker logs -f dispatcharr | grep "Extracted callsign"

# Check stream matches
docker logs -f dispatcharr | grep "Found callsign match"
