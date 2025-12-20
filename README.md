# Stream Mapparr
[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Stream-Mapparr)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Stream-Mapparr?include_prereleases&logo=github)](https://github.com/PiratesIRC/Stream-Mapparr/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Stream-Mapparr/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Stream-Mapparr/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Stream-Mapparr)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Stream-Mapparr)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Stream-Mapparr)
![License](https://img.shields.io/github/license/PiratesIRC/Stream-Mapparr)

A Dispatcharr plugin that automatically matches and assigns streams to channels based on advanced fuzzy matching, quality prioritization, and OTA callsign recognition.

## ⚠️ Important: Backup Your Database
Before installing or using this plugin, it is **highly recommended** that you create a backup of your Dispatcharr database. This plugin makes significant changes to your channel and stream assignments.

**[Click here for instructions on how to back up your database.](https://dispatcharr.github.io/Dispatcharr-Docs/troubleshooting/?h=backup#how-can-i-make-a-backup-of-the-database)**

---

## 🚨 Important: Background Operations & Monitoring
**Please Read Carefully:** This plugin uses **Background Threading** to prevent browser timeouts during long operations. This changes how you interact with the plugin:

1.  **Instant Notification:** The frontend will show a green "✅ Started in background" notification immediately. This does **NOT** mean the task is finished.
2.  **Immediate Re-enabling:** Buttons re-enable instantly so the UI remains responsive. Do not click the button again; the task is active in the background.
3.  **Real-Time Monitoring:** To see progress, ETAs, and completion status, you **must** check your Docker logs.

**To monitor operations, run this in your terminal:**
`docker logs -n 20 -f Dispatcharr | grep plugins`
or
`docker logs -f dispatcharr | grep Stream-Mapparr`

Look for "**✅ [ACTION] COMPLETED**" or "**📄 CSV EXPORT CREATED**" to know when the process is finished.

---

## Features

### Automation & Scheduling
* **Background Processing Engine**: Operations run in threads to prevent HTTP timeouts and "broken pipe" errors on large datasets.
* **Integrated Scheduler**: Configure automated daily runs directly in settings with custom Timezone and HHMM time support.
* **Smart Rate Limiting**: Configurable API throttling with exponential backoff to prevent server overload and 429/5xx errors.
* **Operation Lock System**: Prevents concurrent tasks from running and allows manual clearing of stuck locks after system restarts.

### Matching & Accuracy
* **Advanced Fuzzy Matching**: Automatically finds and assigns streams using an advanced matching engine.
* **Strict Numeric Matching**: Prevents false positives between numbered channels (e.g., ensuring "Sports 1" does not match "Sports 2").
* **US OTA Specialized Matching**: Dedicated action to match US broadcast channels by callsign using authoritative databases.
* **Multi-Country Support**: Support for multiple regional databases (US, UK, CA, NL, etc.) to refine matching accuracy.
* **Customizable Ignore Tags**: Filter out unwanted keywords like `[Backup]` or `West` during the matching process.

### Quality & Stream Health
* **IPTV Checker Integration**: Automatically filters out "dead" streams with 0x0 resolution using metadata from the IPTV Checker plugin.
* **M3U Source Prioritization**: Prioritize streams from specific M3U providers (e.g., premium sources over backup sources) regardless of quality metrics.
* **Resolution & FPS Ranking**: Automatically sorts alternate streams by physical quality (Resolution/FPS) to ensure the best source is primary.
* **Auto-Deduplication**: Automatically removes duplicate stream names during assignment to keep channel lists clean.

### Reporting & Visibility
* **Live Progress Tracking**: Real-time progress engine providing accurate ETAs and minute-by-minute reporting in the logs.
* **Intelligent CSV Analysis**: Reports analyze why channels didn't match and provide specific recommendations (e.g., "Add 'UK' to ignore tags").
* **Dry Run Mode**: Global toggle to preview match results and export reports without making any database changes.
* **Channel Visibility Management**: Automatically enables channels with valid streams and disables those without assignments.

## Requirements
* Active Dispatcharr installation
* Admin username and password for API access
* **A Channels Profile other than "All"**
* Multiple streams of the same channel are available in your setup

## Installation
1.  Log in to Dispatcharr's web UI.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the plugin zip file.
4.  Enable the plugin after installation.

## Updating the Plugin
To update Channel Mapparr from a previous version:

### 1. Remove Old Version
1.  Navigate to **Plugins** in Dispatcharr.
2.  Click the trash icon next to the old Stream Mapparr plugin.
3.  Confirm deletion.

### 2. Restart Dispatcharr
1.  Log out of Dispatcharr.
2.  Restart the Docker container:
    ```bash
    docker restart dispatcharr
    ```

### 3. Install New Version
1.  Log back into Dispatcharr.
2.  Navigate to **Plugins**.
3.  Click **Import Plugin** and upload the new plugin zip file.
4.  Enable the plugin after installation.

### 4. Verify Installation
1.  Check that the new version number appears in the plugin list.
2.  Reconfigure your settings if needed.
3.  Run **Load/Process Channels** to test the update.

## Operations & Monitoring Guide

Because this plugin processes potentially thousands of streams, operations can take 5-15+ minutes.

### How to Run an Action
1.  Open a terminal connected to your server.
2.  Run `docker logs -f dispatcharr` to watch the logs.
3.  In the browser, click an action button (e.g., "Add Stream(s) to Channels").
4.  You will see a notification: **"✅ Operation started in background"**.
5.  **Wait.** Do not close the browser or restart the container.
6.  Watch the terminal logs for updates like:
    * `[Stream-Mapparr] Progress: 20%...`
    * `[Stream-Mapparr] Progress: 50%...`
7.  The operation is finished when you see:
    * `[Stream-Mapparr] ✅ ADD STREAMS TO CHANNELS COMPLETED`

### Why can't I see progress in the UI?
Dispatcharr's plugin system currently supports synchronous HTTP responses. Because we moved to asynchronous background threads (to fix timeouts), the frontend receives the "Started" signal but cannot listen for the "Completed" signal without core modifications to Dispatcharr. We prioritize **successful completion** over UI feedback.

## Scheduling (New in v0.6.0)

You can now schedule Stream-Mapparr to run automatically without setting up external Celery tasks.

1.  Go to **Plugin Settings**.
2.  **Timezone**: Select your local timezone (e.g., `US/Central`, `Europe/London`).
3.  **Scheduled Run Times**: Enter times in 24-hour format, comma-separated (e.g., `0400, 1600`).
    * Example: `0400` runs at 4:00 AM.
    * Example: `0400,1600` runs at 4:00 AM and 4:00 PM.
4.  **Enable CSV Export**: Check this to generate a report every time the scheduler runs.
5.  Click **Update Schedule**.

*Note: The scheduler runs in a background thread. If you restart the Dispatcharr container, the scheduler restarts automatically.*

## Settings Reference

| Setting | Type | Default | Description |
|:---|:---|:---|:---|
| **Overwrite Existing Streams** | `boolean` | True | If enabled, removes all existing streams and replaces with matched streams. |
| **Fuzzy Match Threshold** | `number` | 85 | Minimum similarity score (0-100). Higher = stricter matching. |
| **Dispatcharr URL/Creds** | `string` | - | Connection details for the API. |
| **Profile Name** | `string` | - | Name of the Channel Profile to process. |
| **Channel Groups** | `string` | - | Specific groups to process (empty = all). |
| **Rate Limiting** *(v0.6.0)* | `select` | Medium | Controls API speed. Use 'High' if experiencing timeouts/errors. |
| **Timezone** *(v0.6.0)* | `select` | US/Central | Timezone for scheduled runs. |
| **Scheduled Run Times** | `string` | - | Times to run automatically (HHMM format). |
| **Visible Channel Limit** | `number` | 1 | How many duplicate channels to enable per group. |
| **Ignore Tags** | `string` | - | Tags to strip before matching (e.g., `[Dead], (Backup)`). |

## Channel Databases

Stream-Mapparr uses `*_channels.json` files to improve OTA and cable channel matching.

1.  Navigate to **Settings**.
2.  Scroll to **Channel Databases**.
3.  Check the boxes for the countries you want to enable (e.g., `Enable US`, `Enable UK`).
4.  If you need a custom database, create a JSON file (e.g., `CA_channels.json`) and place it in `/data/plugins/stream_mapparr/`.

## CSV Reports & Recommendations

When running **Preview Changes** or **Add Streams**, the plugin generates a CSV file in `/data/exports/`.

**New in v0.6.0**: Open the CSV file in a text editor or spreadsheet. The header contains detailed analysis:
* **Threshold Recommendations**: "3 additional streams available at lower thresholds. Consider lowering to 75."
* **Token Mismatches**: "Channel 'BBC One' vs Stream 'UK BBC One'. Mismatched token: 'UK'. Add 'UK' to Ignore Tags."

## Troubleshooting

**Buttons are clickable but nothing happens?**
Check the Docker logs. If an operation is already running, the logs will say: `❌ Cannot start... Another operation is already running`. Wait for the current operation to finish (lock expires after 10 mins).

**"API token expired" in logs**
The plugin now automatically attempts to refresh tokens. If it fails, check your Username/Password in settings.

**System/API is slow during scanning**
Change the **Rate Limiting** setting to "High (Slow)". This adds a delay between API calls to reduce load on your server.

**How to stop a running operation?**
Restart the Dispatcharr container: `docker restart dispatcharr`.

**Cleaning up old tasks**
If you upgraded from an older version, run the **"Cleanup Orphaned Tasks"** action to remove old Celery schedules that might conflict with the new internal scheduler.

## Debugging Commands
```bash
# Monitor plugin activity (The most important command!)
docker logs -f dispatcharr | grep "Stream-Mapparr"

# Check generated CSVs
docker exec dispatcharr ls -lh /data/exports/

# Check plugin files
docker exec dispatcharr ls -la /data/plugins/stream_mapparr/
```bash
docker logs -f dispatcharr | grep "Stream-Mapparr"
