# Stream Mapparr
A Dispatcharr plugin that automatically matches and assigns streams to channels based on intelligent name matching, quality prioritization, and timezone filtering.

## Features
* **Intelligent Stream Matching**: Automatically finds and assigns streams to channels using sophisticated name-matching algorithms
* **Quality Prioritization**: Sorts matched streams by quality (4K → FHD → HD → SD → Slow)
* **Timezone Filtering**: Automatically excludes West/Pacific coast feeds unless explicitly requested
* **OTA Channel Support**: Special handling for Over-The-Air broadcast channels using FCC network data
* **Channels.txt Integration**: Distinguishes between similar channel names (e.g., FX vs FXX vs FX Movie Channel)
* **Customizable Ignore Tags**: Filter out unwanted tags during matching
* **Profile & Group Filtering**: Target specific channel profiles and groups
* **Preview Mode**: Dry-run CSV export to review matches before applying changes
* **Individual Channel Updates**: Updates channels one-by-one to avoid API errors
* **Comprehensive Logging**: Detailed matching logic and debug information

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access
* **A Channels Profile other than "All"** ([Profile Documentation](https://dispatcharr.github.io/Dispatcharr-Docs/user-guide/#channels_1))
* Multiple streams of the same channel available in your setup
* `channels.txt` file (curated channel list) - included
* `networks.json` file (FCC broadcast station database) - included for OTA support

## Recommended Prerequisite
It is **highly recommended** to standardize your channel names first using the [Channel Mapparr Plugin](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin). Standardized channel names significantly improves matching accuracy.

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

## Usage Guide

### Step-by-Step Workflow
1. **Configure Settings**
   * Enter your Dispatcharr URL, username, and password
   * Specify an existing **Profile Name** (cannot be "All")
   * Optionally specify **Channel Groups** (leave empty to process all)
   * Optionally configure **Ignore Tags** to filter out unwanted tags
   * Click **Save Settings**

2. **Load and Process Channels**
   * Click **Run** on `Load/Process Channels`
   * The plugin loads channels from the specified profile and groups
   * Fetches all available streams (handles pagination automatically)
   * Review the summary showing channel and stream counts

3. **Preview Changes (Dry Run)**
   * Click **Run** on `Preview Changes (Dry Run)`
   * Exports a CSV to `/data/exports/channel_addarr_preview_YYYYMMDD_HHMMSS.csv`
   * Review matched streams for each channel
   * Identifies channels without matches

4. **Add Streams to Channels**
   * Click **Run** on `Add Stream(s) to Channels`
   * Replaces existing stream assignments with matched streams
   * Updates channels individually to avoid bulk API errors
   * Triggers M3U refresh to update the GUI
   * Exports results to `/data/exports/channel_addarr_results_YYYYMMDD_HHMMSS.csv`

## Matching Logic

### Standard Channel Matching
The plugin uses a multi-stage matching process:

1. **Exact Match After Prefix**: Matches channel names that appear after prefixes like "US:", "USA:", etc.
   * Example: "TBS [FHD]" → "US: TBS", "USA: TBS"

2. **Word Boundary Matching**: Ensures complete word matches to avoid false positives
   * Example: "FX" matches "US: FX" but NOT "FXX" or "WFXR"

3. **Longer Channel Filtering**: Uses `channels.txt` to distinguish similar names
   * Example: "FX" does NOT match "FX Movie Channel" (different channel)

4. **Call Sign Filtering**: Excludes local TV station call signs
   * Example: "FX" does NOT match "WBKB FX (WBKBDT4)" (local FOX affiliate)

5. **Timezone Filtering**: Automatically excludes West/Pacific feeds
   * Example: "SYFY [HD]" matches "US NBC SYFY (East)" but NOT "US NBC SYFY (West)"

### OTA Channel Matching
For broadcast channels like "ABC - TN Chattanooga (WTVC)":

1. **Callsign Extraction**: Parses network, state, city, and callsign
2. **Networks.json Lookup**: Matches against FCC broadcast database
3. **Exact Callsign Matching**: Uses word boundaries to prevent partial matches
   * Example: "KOB" matches "NBC (KOB)" but NOT "NBC (KOBI)"
4. **Network Validation**: Confirms stream contains the correct network affiliation

### Quality Sorting
Matched streams are automatically sorted by quality precedence:
4K → [4K] → (4K) → FHD → [FHD] → (FHD) → HD → [HD] → (HD) → SD → [SD] → (SD) → Slow → [Slow] → (Slow)

## Example Transformations

### Standard Channels
**Channel**: `TBS [FHD]`  
**Matched Streams**: `USA: TBS`, `US: TBS`, `US: TBS`

**Channel**: `FX (East) [HD]`  
**Matched Streams**: `US: FX`, `US: FX`, `US FX (East) (S)`, `US FX (East) (H)`, `US FX (East) (A)`, `US (F2) FX (East)`  
**Excluded**: `US: FX Movie Channel`, `(US) (FOX1) WBKB FX (WBKBDT4)`

**Channel**: `SYFY [HD]`  
**Matched Streams**: `USA SYFY`, `US: SYFY`, `US: SYFY`, `US NBC SYFY (East) (D)`  
**Excluded**: `US NBC SYFY (West) (D)`

### OTA Channels
**Channel**: `NBC - NM Albuquerque (KOB)`  
**Matched Streams**: `US NBC (KOB) Albuquerque, NM (D)`, `NBC: NM ALBURQUERQUE KOB`  
**Excluded**: `US NBC (KOBI) Medford` (different callsign)

## Action Reference

| Action | Description |
|:-------|:------------|
| **Load/Process Channels** | Load channels from specified profile/groups and fetch all available streams |
| **Preview Changes (Dry Run)** | Export CSV showing which streams will be matched to each channel |
| **Add Stream(s) to Channels** | Apply matched streams to channels, replacing existing assignments |

## File Locations
* **Processing Cache**: `/data/channel_addarr_processed.json`
* **Preview Export**: `/data/exports/channel_addarr_preview_YYYYMMDD_HHMMSS.csv`
* **Results Report**: `/data/exports/channel_addarr_results_YYYYMMDD_HHMMSS.csv`
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

### Results CSV
| Column | Description |
|:-------|:------------|
| **channel_id** | Internal Dispatcharr channel ID |
| **channel_name** | Channel name |
| **channel_number** | Channel number |
| **matched_streams_count** | Number of streams added |
| **stream_names** | Pipe-separated list of added stream names |
| **stream_ids** | Comma-separated list of added stream IDs |
| **status** | `Updated` or `No matches` |

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

**Channels not matching streams**
* Run Channel Mapparr first to standardize channel names
* Check that streams exist for your channels
* Use Preview mode to see matching details in logs
* Verify `channels.txt` contains your channel names

**Too many false positives**
* Add entries to `channels.txt` to distinguish similar names
* Use Ignore Tags to filter out quality indicators
* Check logs for matching logic details

**OTA channels not matching**
* Verify channel name format: `NETWORK - STATE City (CALLSIGN)`
* Check that callsign exists in `networks.json`
* Ensure network affiliation is correct
* Review logs for callsign extraction details

### Debugging Commands
```bash
# Check plugin files
docker exec dispatcharr ls -la /data/plugins/stream_mapparr/

# Monitor plugin activity
docker logs -f dispatcharr | grep -i stream_mapparr

# View processing cache
docker exec dispatcharr cat /data/channel_addarr_processed.json | jq
```

### Data Sources
channels.txt Format
A plain text file with one channel name per line:
TBS
PixL
FX
FXX
FX Movie Channel
HBO
HBO2

### `networks.json` Format
An FCC broadcast station database with fields:
* `callsign`: Station identifier (e.g., WTVC, KABC-TV)
* `community_served_city`: City name
* `community_served_state`: Two-letter state code
* `network_affiliation`: Network name(s), may contain multiple values
* `tv_virtual_channel`: Virtual channel number
* `facility_id`: FCC facility identifier

**Network Affiliation Parsing Rules**:
* Multiple networks separated by comma, semicolon, or slash - only first is used
* Format like "CBS (12.1), CW (12.2)" - only CBS is used
* Format like "WTOV D1 - NBC; WTOV D2 - FOX" - only NBC is used
* Callsign suffixes after dash are removed (e.g., "WLNE-TV" → "WLNE")

## Performance Notes
* Handles pagination automatically (fetches all streams across multiple pages)
* Processes up to 3,600+ streams efficiently
* Updates channels individually to avoid bulk API errors
* Automatic M3U refresh triggers GUI updates
* Detailed logging for debugging and transparency

## Contributing
When reporting issues, please include:
* Dispatcharr version
* Sample channel names that fail to match
* Relevant container logs showing matching attempts
* Contents of the preview CSV (first 10 rows)
* Whether you're using Channel Mapparr for name standardization

## Related Projects
* [Channel Mapparr](https://github.com/PiratesIRC/Dispatcharr-Channel-Maparr-Plugin) - Standardize channel names before running Stream Mapparr

## License
This plugin integrates with Dispatcharr's plugin system and follows its licensing terms.
