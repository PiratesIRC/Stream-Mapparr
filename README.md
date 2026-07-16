# Stream-Mapparr

> [!TIP]
> **New to Dispatcharr plugins?** Start with the **[Dispatcharr Plugin Workflow guide](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/)**.
> It explains what each plugin and tool does, where they overlap, and what order to use them in.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Stream-Mapparr)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/workflow/03-stream-mapparr/)
[![Discord](https://img.shields.io/badge/Discord-Discussion-5865F2?logo=discord&logoColor=white)](https://discord.gg/Sp45V5BcxU)

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
- Optional **webhook** POST to an HTTP(S) endpoint of your choice (see settings) — Discord and Slack supported natively
- WebSocket event to the frontend

While a long operation runs, the plugin also pushes a **"started" toast**, periodic **`% + ETA`** toasts (adaptive cadence), and a **completion** toast. You can check on a run at any time:

- **📊 View Check Progress** — current action, percentage, and ETA of the running operation
- **📋 View Last Results** — summary (counts, CSV filename, duration) of the last completed run

Buttons re-enable instantly. Do not click again while an operation is in flight — an operation lock prevents concurrent runs and auto-expires after 10 minutes.

---

## Features

### Matching
- **Multi-stage fuzzy matching**: Exact, substring, and token-sort matching with configurable sensitivity (Relaxed/Normal/Strict/Exact)
- **Channel-name aliases**: A built-in US alias table plus a user-editable **Custom Aliases** JSON setting force-include known aliases into a channel's matches, independent of the fuzzy threshold
- **Regex pre-processing** (opt-in, issue #36): User-supplied `[find, replace]` regex rules run on stream names before normalization and aliases — a general escape hatch for provider junk (decorative glyphs, invisible padding, bouquet prefixes) that doesn't fit an existing built-in fix. Matching only; see [Regex pre-processing](#regex-pre-processing) below
- **Stylized-name normalization**: Matches streams whose names use stylized-Unicode markers (superscript/small-caps such as `ᴿᴬᵂ`/`ꜰʜᴅ`), emoji-as-letters (`beIN SP⚽RTS` → SPORTS), or numeric resolution tags (`3840P`/`1080p`) — these are stripped or normalized before matching, collision-safe and non-Latin-safe
- **Box-bar tag/delimiter stripping**: Provider and country tags built from box-bar characters `┃` (U+2503) and `│` (U+2502) are stripped before matching — a leading bouquet tag (`┃CANAL+┃ NPO 1` → `NPO 1`), a box bar used as a colon after a country code (`NL┃ NPO 1` → `NPO 1`), and matched pairs (`┃US┃` / `│US│`). A stray single bar is left alone
- **Invisible zero-width character stripping**: Some providers pad stream names with invisible Unicode format characters (zero-width space `U+200B`, zero-width joiners, word joiner `U+2060`, BOM `U+FEFF`, soft hyphen, bidi marks) — often wrapped around a decorative block glyph like `▎` (`UK ␣▎␣BBC 1 FHD`). These are matched by neither a normal space nor the visible-symbol strip, so they used to survive and silently wreck matching for the whole provider; they are now removed up front, so `UK ▎BBC 1 FHD` matches `BBC 1`
- **Non-Latin name preservation**: Cyrillic, CJK, and Arabic channel names are kept through normalization (alphanumerics of any script survive) instead of being erased, so they match on their actual characters rather than colliding as empty strings
- **US OTA callsign matching**: US broadcast affiliates (e.g. `ABC - TX Dallas (WFAA)`) are matched by their FCC callsign — automatically during **Match & Assign** and via the dedicated **Match US OTA Only** action — using the bundled FCC station table (`networks.json`, ~1,900 stations)
- **Multi-country channel databases**: US, UK, CA, AU, BR, DE, ES, FR, IN, MX, NL, NO
- **Zone-based channel variants**: East/West feeds for 33 major cable networks (FX, FXX, USA, Syfy, Disney Channel, etc.) via JSON `"zones"` array expansion
- **Zone-aware East/West routing**: When you keep separate East and West channels (for example `Starz Encore` and `STARZ Encore (W)`), each channel is given its matching feed as the primary stream. West streams lead on the West channel; East, national, and unmarked channels lead with their East/national feed and keep any West streams as failover — so an unmarked channel like `Cinemax` never leads with the West-coast (Pacific-time) feed. A `Pacific` / `(Pacific)` / `(PT)` marker counts as West. This applies in Match & Assign, Sort, and Preview, and is a no-op for single-feed channels (whose streams are all one zone).
- **Country-restricted matching** (opt-in): Only match streams from the same detected country/group — e.g. `CANADA/CA` channels match only `CANADA/CA` streams
- **Normalization cache**: Pre-normalizes stream names once for batch matching performance
- **rapidfuzz acceleration**: Uses C-accelerated Levenshtein when available (20-50x faster), with pure Python early-termination fallback
- **Bulk ORM writes**: Stream assignments go through `bulk_create`, collapsing per-row DB round-trips to one query per channel

### Quality & Streams
- **Quality-based stream sorting**: 4K > UHD > FHD > HD > SD, using probed resolution (from IPTV Checker) or name-based detection
- **Throughput-based stream sorting** (v1.26.1171629+): Measure each source's sustained throughput against its nominal bitrate, then rank `healthy → marginal → unknown → insufficient`. Catches under-delivering sources that share the same advertised resolution as healthy peers. Probes are serialized per M3U account, globally rate-limited, and cached on disk with a configurable TTL — sort never blocks on the network.
- **Audio priority sorting** (v1.26.1362122+): Optionally rank streams by audio channel layout and codec. Two opt-in comma-separated lists (most-preferred first, e.g. `7.1, 5.1, stereo` and `eac3, ac3, aac`) using case-insensitive substring match against `stream_stats` audio info (from IPTV Checker, no probing). Applied after the video resolution/FPS tier; channel layout ranked before codec; unlisted/missing sorts last. Blank = disabled (no behavior change on upgrade).
- **M3U source prioritization**: Prefer streams from specific M3U providers
- **Dead stream filtering**: Skip streams with 0x0 resolution (requires IPTV Checker)
- **Auto-deduplication**: Removes duplicate stream names during assignment

### Automation
- **Built-in scheduler**: Configure daily runs at one or more HHMM times. The timezone follows Dispatcharr's global setting. Scheduled runs are multi-worker safe, so the job runs once per slot even when Dispatcharr is running several worker processes.
- **Auto-match after M3U refresh** (opt-in): Run Match & Assign automatically as soon as an M3U refresh completes, so new streams get matched without waiting for the next scheduled slot. Requires a Channel Profile to be selected. Concurrent per-account refreshes are coalesced under a cross-worker lock, and accounts that finish mid-match trigger a follow-up pass so nothing is missed. Inert on Dispatcharr < 0.27 (the scheduler remains the trigger there).
- **Rate limiting**: Configurable throttling (None/Low/Medium/High)
- **Operation lock**: Prevents concurrent tasks; auto-expires after 10 minutes
- **Dry run mode**: Preview results with CSV export without making changes
- **Webhook notifications**: POST a summary (action, counts, CSV filename, dry-run flag) to any HTTP(S) endpoint on completion. **Discord** and **Slack** webhook URLs are auto-detected and sent in their native message shape (`{"content"}` / `{"text"}`); any other endpoint receives the generic JSON payload — wire into Home Assistant, ntfy, etc.
- **Live progress + last-results**: `📊 View Check Progress` and `📋 View Last Results` actions report the running operation's %/ETA and the last completed run's summary, backed by on-disk state so they work across UI sessions.

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
| **Overwrite Existing Streams** | boolean | True | Replace existing streams vs append-only. A run that matches **0** streams never clears a channel's existing streams |
| **Match Sensitivity** | select | Normal (80) | Relaxed (70), Normal (80), Strict (90), Exact (95) |
| **Channel Profile** | select | - | Profile to process channels from (dropdown from DB) |
| **Channel Groups** | string | (all) | Specific groups to process, comma-separated |
| **Stream Groups** | string | (all) | Specific stream groups to use, comma-separated |
| **M3U Sources** | string | (all) | Specific M3U sources, comma-separated (order = priority) |
| **Custom Aliases** | string | (none) | JSON object of extra `"channel": ["alias", …]` mappings, merged with the built-in alias table |
| **Stream Name Regex Rules** | string | (none) | JSON list of `[find, replace]` regex pairs applied in order to stream names before matching (e.g. `[["\s*▎\s*", " "], ["\bVIP\b", ""]]`). Python regex syntax; use `(?i)` for case-insensitive. Matching only — see [Regex pre-processing](#regex-pre-processing) below |
| **Prioritize Quality** | boolean | False | Sort by quality first, then M3U source priority |
| **Custom Ignore Tags** | string | (none) | Tags to strip before matching (e.g., `[Dead], (Backup)`) |
| **Tag Handling** | select | Strip All | Strip All / Keep Regional / Keep All |
| **Channel Database** | select | US | Channel database for callsign and name matching |
| **Visible Channel Limit** | number | 1 | Channels per group to enable and assign streams |
| **Rate Limiting** | select | None | None / Low / Medium / High |
| **Filter Dead Streams** | boolean | False | Skip 0x0 resolution streams (requires IPTV Checker) |
| **Restrict Matching To Same Country** | boolean | False | Only match streams whose detected country matches the channel's country/group |
| **Webhook URL** | string | (blank) | HTTP(S) endpoint to POST JSON summary on completion (see below) |
| **Fire Webhook On Completion** | boolean | False | Enable webhook delivery for Match & Assign / Match OTA / Sort Streams |
| **Scheduled Run Times** | string | (none) | HHMM times, comma-separated (e.g., `0400,1600`) |
| **Auto-match after M3U refresh** | boolean | False | Run Match & Assign automatically after each M3U refresh completes (opt-in; requires a Channel Profile). Dispatcharr v0.27+ |
| **Dry Run Mode** | boolean | False | Preview without making database changes |
| **Enable Throughput-Based Sorting** | boolean | True | Prepend a measured-throughput tier to alternate-stream sorting. Falls back to resolution/FPS when no probe data is available. |
| **Probe Duration (seconds)** | number | 8 | Length of each throughput probe — long enough to clear TCP slow-start, short enough to bound the run. |
| **Probe Cache TTL (minutes)** | number | 30 | How long a measurement is considered fresh. Stale entries are re-probed on the next run; sort treats stale entries as unknown. |
| **Probe Rate (probes / minute)** | number | 6 | Global cap on probes initiated per minute. Probes are also serialized per M3U account with a 1-second gap. |
| **Bitrate Safety Margin** | string | 1.10 | Multiplier on nominal bitrate. Throughput < nominal × margin → `insufficient`; < nominal × 1.5 → `marginal`; otherwise `healthy`. |
| **Audio Channels Priority** | string | "" | Comma-separated audio channel layouts, most preferred first (e.g. `7.1, 5.1, stereo, mono`). Case-insensitive substring match; unlisted/missing sorts last. Ranked before codec. Blank = disabled. |
| **Audio Codec Priority** | string | "" | Comma-separated audio codecs, most preferred first (e.g. `eac3, ac3, aac, mp2`). Case-insensitive substring match; unlisted/missing sorts last. Ranked after channels. Blank = disabled. |

## Actions

| Action | Description |
|:---|:---|
| **Validate Settings** | Check configuration, profiles, groups, databases |
| **Test Regex Rules** | Preview what Stream Name Regex Rules would change, against every stream currently loaded (ignores Selected Groups / M3U scoping) — per-rule status, an N-of-M changed count, and up to 20 before → after samples with invisible characters escaped so they're actually visible |
| **Load/Process Channels** | Load channel and stream data from database |
| **Preview Changes** | Dry-run with CSV export |
| **Match & Assign Streams** | Fuzzy match and assign streams to channels |
| **Match US OTA Only** | Match US broadcast channels by callsign |
| **Sort Alternate Streams** | Re-sort existing streams by quality (and throughput tier, when probe data is available). CSV gains `tiers`, `throughput_mbps`, and `edge_ips` columns aligned with `stream_names`. |
| **Probe Stream Throughput** | Measure sustained throughput for streams currently assigned to channels in the selected profile + groups. Updates `/data/stream_mapparr_throughput_cache.json`. Run before *Sort Alternate Streams* to feed measured Mbps into the sort. |
| **Manage Channel Visibility** | Enable/disable channels based on stream count |
| **View Check Progress** | Show the current operation's progress (%) and ETA |
| **View Last Results** | Show a summary of the last completed operation |
| **Clear CSV Exports** | Delete all plugin CSV files |

## Regex pre-processing

Some providers pad or decorate stream names in ways that defeat fuzzy matching — decorative
glyphs, invisible padding, bouquet prefixes, badges. **Stream Name Regex Rules** is a general
escape hatch: user-supplied regex find/replace rules run on stream names before anything else
touches them, so novel provider junk doesn't need a plugin release to fix.

**Example (issue #36):** a provider padded stream names with an invisible zero-width space
wrapped around a decorative block glyph — `UK ▎BBC 1 FHD`, where `▎` (U+258E) is flanked by
invisible characters a normal eyeball never sees. A rule like `[["\\s*▎\\s*", " "]]` collapses
that back to `UK BBC 1 FHD`, which then normalizes and matches `BBC 1` normally.

Pipeline order:

```
raw stream name → [regex rules] → normalization / ignore tags → aliases → fuzzy match
```

Rules run **before** normalization/ignore-tag stripping and **before** Custom Aliases, matching
the order requested in the issue. Rules apply in the order given in the JSON list — later rules
see earlier rules' output.

Two scope notes:

- **Quality sorting, zone routing, country restriction and duplicate detection read the
  original name — rules affect matching only.** Stream names in Dispatcharr are never
  modified, and identity/ordering logic (duplicate detection's stream key, country-restriction's
  country detection, zone routing, quality sort) all key on the untouched name, so a rule that,
  say, strips a country prefix for matching purposes can't also blind country detection or
  collapse two genuinely distinct failover streams into one.
- **Group labels are out of scope.** Selected Groups and country restriction read *group* names
  literally — rules only ever touch stream *names*, never group labels or channel names.

Feedback loop: **Validate Settings** reports a one-line summary (`N ok, M rejected`) with full
per-rule detail in the logs; the **🧪 Test Regex Rules** action (see Actions above) shows real
before → after samples — with invisible characters escaped so they're actually visible — against
every currently-loaded stream, without changing anything.

Guardrails: a rule is statically rejected up front if it contains a nested unbounded quantifier
or alternation shape known to backtrack exponentially (e.g. `(a+)+`). At runtime, names over 500
characters skip regex pre-processing, a rule chain that grows a name past 4x its original length
is reverted and stopped, and an entire regex pass is capped at 5 seconds cumulative — so a bad
rule degrades gracefully (and is reported) instead of freezing a worker.

## Scheduling

1. Set **Scheduled Run Times** in 24-hour format (e.g., `0400,1600` for 4 AM and 4 PM) — times are interpreted in Dispatcharr's **global timezone** (no plugin timezone setting)
2. Enable **CSV Export** if desired
3. Click **Update Schedule**

The scheduler runs in a background thread and re-arms automatically when the container restarts. When Dispatcharr runs several worker processes, a shared on-disk claim makes sure the scheduled job runs once per slot rather than once per worker.

**Event-driven alternative (Dispatcharr v0.27+):** enable **Auto-match after M3U refresh** to run Match & Assign the moment an M3U refresh finishes, instead of (or in addition to) fixed times. It subscribes to Dispatcharr's `m3u_refresh` event; because that event fires once per M3U account, the runs are coalesced under a cross-worker lock and a follow-up pass captures any account that finishes mid-match, so a multi-account "Refresh All" produces one effective match rather than one per account.

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

**Discord and Slack** are auto-detected by URL and receive their native body shape instead of the payload above, so delivery actually succeeds (a generic body returns HTTP 400 from those platforms):

- Discord (`https://discord.com/api/webhooks/…`) → `{"content": "<message>"}` (truncated to 2000 chars)
- Slack (`https://hooks.slack.com/…`) → `{"text": "<message>"}`

Any other endpoint receives the full generic JSON payload above. The URL is fetched server-side from Dispatcharr — only enter endpoints you trust.

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
