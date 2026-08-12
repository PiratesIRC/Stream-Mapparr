# Stream-Mapparr

> [!TIP]
> **New to Dispatcharr plugins?** Start with the **[Dispatcharr Plugin Workflow guide](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/)**.
> It explains what each plugin and tool does, where they overlap, and what order to use them in.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/PiratesIRC/Stream-Mapparr)
[![Workflow Guide](https://img.shields.io/badge/%F0%9F%93%96-Workflow_Guide-1F6FEB?style=flat)](https://piratesirc.github.io/Dispatcharr-Plugin-Workflow/workflow/03-stream-mapparr/)
[![Discord](https://img.shields.io/badge/Discord-Discussion-5865F2?logo=discord&logoColor=white)](https://discord.gg/Sp45V5BcxU)
[![Sponsor](https://img.shields.io/badge/Sponsor-%E2%9D%A4-db61a2?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/PiratesIRC)

[![GitHub Release](https://img.shields.io/github/v/release/PiratesIRC/Stream-Mapparr?include_prereleases&logo=github)](https://github.com/PiratesIRC/Stream-Mapparr/releases)
[![Downloads](https://img.shields.io/github/downloads/PiratesIRC/Stream-Mapparr/total?color=success&label=Downloads&logo=github)](https://github.com/PiratesIRC/Stream-Mapparr/releases)

![Top Language](https://img.shields.io/github/languages/top/PiratesIRC/Stream-Mapparr)
![Repo Size](https://img.shields.io/github/repo-size/PiratesIRC/Stream-Mapparr)
![Last Commit](https://img.shields.io/github/last-commit/PiratesIRC/Stream-Mapparr)
![License](https://img.shields.io/github/license/PiratesIRC/Stream-Mapparr)

A Dispatcharr plugin that automatically matches and assigns streams to channels
using fuzzy matching, quality prioritization, and OTA callsign recognition.

## Backup Your Database

Before installing or using this plugin, create a backup of your Dispatcharr
database. This plugin modifies channel and stream assignments.

**[Backup instructions](https://dispatcharr.github.io/Dispatcharr-Docs/user-guide/?h=backup#backup-restore)**

---

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

## Further reading

| Guide | What it covers |
|:---|:---|
| [Regex pre-processing](docs/regex-preprocessing.md) | Writing find and replace rules for provider junk in stream names |
| [Notifications and emailed reports](docs/notifications.md) | Sending HTML and CSV reports through Newsflasharr |
| [Troubleshooting](docs/troubleshooting.md) | Stuck operations, no matches, slates, useful commands |

---

## Features

### Matching

- **Multi-stage fuzzy matching**: exact, substring and token-sort, with
  configurable sensitivity (Relaxed, Normal, Strict, Exact)
- **Channel-name aliases**: a built-in US alias table plus a user-editable
  **Custom Aliases** setting. Aliases are force-included regardless of the fuzzy
  threshold, and are matched case-insensitively
- **Regex pre-processing** (opt-in): your own find and replace rules run on
  stream names before anything else. See
  [Regex pre-processing](docs/regex-preprocessing.md)
- **Stylized-name normalization**: handles superscript and small-caps markers
  such as `ᴿᴬᵂ`, emoji used as letters (`beIN SP⚽RTS`), and numeric resolution
  tags like `3840P`
- **Box-bar tag stripping**: removes provider and country tags built from `┃`
  and `│`, such as `┃CANAL+┃ NPO 1` or `NL┃ NPO 1`. A stray single bar is left
  alone
- **Invisible character stripping**: removes zero-width spaces, joiners, word
  joiners, byte order marks, soft hyphens and bidi marks. Providers use these as
  padding, often around a decorative glyph, and they used to wreck matching for
  a whole provider without being visible to anyone
- **Non-Latin name preservation**: Cyrillic, CJK and Arabic names survive
  normalization and match on their own characters instead of collapsing to empty
  strings
- **US OTA callsign matching**: US broadcast affiliates such as
  `ABC - TX Dallas (WFAA)` are matched by FCC callsign, using a bundled station
  table of around 1,900 stations
- **Multi-country channel databases**: US, UK, CA, AU, BR, DE, ES, FR, IN, MX,
  NL, NO
- **Country-restricted matching** (opt-in): only match streams whose detected
  country matches the channel's
- **Performance**: a normalization cache, C-accelerated Levenshtein through
  rapidfuzz where available, and bulk database writes

### East and West feeds

- A channel receives its own zone and unmarked feeds only. An unmarked channel
  such as `Cinemax` is treated as the East or national feed
- **Opposite-zone streams are not attached at all.** A West feed is three hours
  behind, so a failover to it on a movie channel plays a different film
- If excluding would leave a channel with no streams, the streams are kept and a
  warning is logged, because emptying a channel takes it off the air
- `Pacific`, `(Pacific)` and `(PT)` all count as West
- A marker only counts when it stands alone, meaning bracketed, last word, or
  followed only by a quality tag. `US: ABC 25 (WPBF) West Palm Beach HD` is a
  place, not a West feed
- The word is ignored entirely for countries with one time zone, so
  `UK: BBC ONE WEST` stays attached to BBC One as the English region it is

### Quality and streams

- **Quality-based sorting**: 4K, UHD, FHD, HD, SD, using probed resolution where
  available or the name otherwise
- **Throughput-based sorting**: measures each source's sustained throughput
  against its nominal bitrate and ranks it healthy, marginal, unknown or
  insufficient. Probes are serialized per M3U account, rate-limited, and cached
  on disk, so sorting never blocks on the network
- **Placeholder demotion**: a stream claiming 720p or more while carrying almost
  no video is ranked last. This catches looping slates and static cards, which
  throughput measurement cannot, because a tiny payload arrives quickly
- **Audio priority sorting** (opt-in): rank by audio channel layout and codec,
  using two comma-separated preference lists. Applied after the video tier, with
  layout ranked before codec
- **M3U source prioritization**: prefer streams from specific providers
- **Dead stream filtering**: skip streams with 0x0 resolution
- **Auto-deduplication**: collapse duplicate stream names during assignment

### Automation

- **Built-in scheduler**: daily runs at one or more times, in Dispatcharr's
  global timezone. Safe across multiple worker processes, so a job runs once per
  slot rather than once per worker
- **Auto-match after M3U refresh** (opt-in, Dispatcharr v0.27+): run Match and
  Assign as soon as a refresh completes. Requires a Channel Profile
- **Rate limiting**: None, Low, Medium or High
- **Operation lock**: prevents concurrent tasks, auto-expiring after 10 minutes
- **Dry run mode**: preview results with a CSV export and no changes
- **Emailed reports**: an HTML report and a CSV through Newsflasharr. See
  [Notifications](docs/notifications.md)
- **Live progress**: **View Check Progress** and **View Last Results** report
  the running operation and the last completed one, backed by on-disk state so
  they survive a UI reload

## How a run reports back

Small jobs run synchronously and the Dispatcharr interface shows the real
result, including channel and stream counts and the CSV filename.

Larger jobs run in a background thread. The interface shows a "started in
background" notification, and completion arrives by:

- Docker logs: `docker logs -f dispatcharr | grep Stream-Mapparr`, looking for
  `COMPLETED`
- An optional emailed report through Newsflasharr
- A WebSocket event to the frontend

While a long operation runs, the plugin pushes a started notification, periodic
percentage and estimated-time notifications, and a completion notification.

Buttons re-enable immediately. Do not click again while an operation is running:
the operation lock prevents concurrent runs and auto-expires after 10 minutes.

## Settings

| Setting | Type | Default | Description |
|:---|:---|:---|:---|
| **Overwrite Existing Streams** | boolean | True | Replace existing streams rather than appending. A run matching 0 streams never clears a channel |
| **Match Sensitivity** | select | Normal (80) | Relaxed (70), Normal (80), Strict (90), Exact (95) |
| **Channel Profile** | select | - | Profile to process channels from |
| **Channel Groups** | string | (all) | Groups to process, comma-separated. Empty means all groups |
| **Channel Groups Mode** | select | Only the groups listed | Whether the list names the groups to process or the groups to skip. Choosing to skip means a group you create later is processed automatically. Empty list means all groups either way |
| **Stream Groups** | string | (all) | Stream groups to draw candidate streams from, comma-separated |
| **Stream Groups Mode** | select | Only the groups listed | The same choice for stream groups, resolved separately from the channel-group list |
| **M3U Sources** | string | (all) | M3U sources to use, comma-separated. Order sets priority |
| **Custom Aliases** | string | (none) | JSON object of extra `"channel": ["alias", …]` mappings. Channel names and aliases are both matched case-insensitively, and whitespace around a channel name is ignored |
| **Stream Name Regex Rules** | string | (none) | JSON list of `[find, replace]` pairs applied to stream names before matching. See [Regex pre-processing](docs/regex-preprocessing.md) |
| **Prioritize Quality** | boolean | False | Sort by quality first, then by M3U source priority |
| **Custom Ignore Tags** | string | (none) | Tags to strip before matching, for example `[Dead], (Backup)` |
| **Wait for IPTV Checker Completion** | boolean | False | Hold a scheduled run until IPTV Checker has finished, so matching sees fresh stats |
| **IPTV Checker Max Wait (hours)** | number | 2 | How long to wait before running anyway |
| **Enable CSV Export** | boolean | True | Write a CSV on a scheduled Match and Assign run. A dry run always writes one |
| **Tag Handling** | select | Strip All | Strip All, Keep Regional, or Keep All |
| **Channel Database** | select | US | Which channel database to use. You choose this, never your provider's country prefix, so one database per country serves every provider and none needs duplicating |
| **Visible Channel Limit** | number | 1 | Channels per group to enable and assign streams to |
| **Rate Limiting** | select | None | None, Low, Medium or High |
| **Filter Dead Streams** | boolean | False | Skip 0x0 resolution streams. Requires IPTV Checker |
| **Restrict Matching To Same Country** | boolean | False | Only match streams whose detected country matches the channel's. `GB:` is accepted as well as `UK:`, and `DR:` as well as `DO:`. An unrecognised prefix is treated as unknown and the stream is kept, never dropped |
| **Stream Prefix Countries** | string | (empty) | Tell the country filter what a provider prefix means, as comma-separated `PREFIX=COUNTRY` entries such as `NOW=UK, GO=US`. Use it when a prefix names a platform rather than a country, which the plugin cannot know: NOW is Sky's service in the United Kingdom and also in Italy, so no default is right for everyone. Consulted last, so it fills a gap and never overrules a country the provider stated. Matches only at the start of a name, never a word inside a title |
| **Keep Same-Named Streams From One Source** | boolean | False | Enable if your provider publishes several genuinely different feeds under one identical name. By default those are treated as duplicates |
| **Enable EPG-Based Placeholder Matching** | boolean | False | Match a placeholder-named channel or stream by the programme currently airing on it, taken from EPG data, instead of by its literal name. For providers that name event slots generically, such as `PPV EVENT 04`, and put the real event only in the guide. Channel and stream names are never modified |
| **Placeholder Name Patterns** | string | (see plugin) | One regex per line. A name is only ever treated as a placeholder if it matches one of these, so nothing else changes behaviour |
| **EPG Title Cleanup Rules** | string | (see plugin) | JSON list of `[find, replace]` pairs applied to the raw programme title before it is used for matching, for example stripping a `Next Event: X at 6:00AM` wrapper down to `X` |
| **Skip Titles** | string | (see plugin) | Comma-separated. If the cleaned programme title matches one of these, the channel keeps its literal name for that pass, because an idle slot carries no useful event |
| **Channel Schedule Suffix Cleanup Rules** | string | (see plugin) | JSON list of `[find, replace]` pairs that strip a schedule annotation such as `\| Monday @ 5` from the channel name before it is compared against a programme title |
| **EPG Event Watch Source Streams** | string | (see plugin) | Comma-separated names of permanently-named source streams to watch for an event, rather than placeholder-named ones |
| **Send notifications to Newsflasharr** | boolean | False | Emit notifications through the Newsflasharr plugin |
| **Email A Report After** | select | Scheduled runs only | Which runs email a report: never, scheduled only, or every run |
| **Email Report Format** | select | Both | Which file is emailed. One report sends ONE email carrying one attachment, because a notification can carry only one. Both writes both files and emails the HTML page; the email names the file it did not attach |
| **Scheduled Run Times** | string | (none) | Times in HHMM, comma-separated, for example `0400,1600` |
| **Auto-match after M3U refresh** | boolean | False | Run Match and Assign after each M3U refresh. Requires a Channel Profile. Dispatcharr v0.27+ |
| **Dry Run Mode** | boolean | False | Preview without making database changes |
| **Enable Throughput-Based Sorting** | boolean | True | Add a measured-throughput tier to alternate-stream sorting, falling back to resolution when no probe data exists |
| **Probe Duration (seconds)** | number | 8 | Length of each throughput probe |
| **Probe Cache TTL (minutes)** | number | 30 | How long a measurement stays fresh. Stale entries sort as unknown |
| **Probe Rate (probes / minute)** | number | 6 | Global cap on probes per minute. Also serialized per M3U account |
| **Bitrate Safety Margin** | string | 1.10 | Multiplier on nominal bitrate. Below nominal times this is `insufficient`, below nominal times 1.5 is `marginal` |
| **Demote Placeholder Streams** | boolean | True | Rank a stream last when it claims 720p or higher but carries less video than the floor below. Streams are moved down the order, never removed, and one with no measured bitrate is never affected |
| **Placeholder Bitrate Floor (kbps)** | number | 300 | The floor for the setting above. Standard definition is never checked. Where two bitrate figures disagree the higher is used, so a disagreement keeps the stream |
| **Audio Channels Priority** | string | "" | Audio layouts, most preferred first, for example `7.1, 5.1, stereo`. Ranked before codec. Blank disables it |
| **Audio Codec Priority** | string | "" | Audio codecs, most preferred first, for example `eac3, ac3, aac`. Ranked after layout. Blank disables it |

## Actions

| Action | Description |
|:---|:---|
| **Validate Settings** | Check configuration, profiles, groups and databases |
| **Test Regex Rules** | Preview what your regex rules would change, with before and after samples and invisible characters made visible. Writes the full readout to `/config/stream-mapparr/test-regex-rules.txt`, since a notification shows only about 280 characters |
| **Check Stream Country Labels** | Compare each stream's group country against its EPG identifier suffix and report where they disagree. Reads two database columns, opens no provider connection and changes nothing. A disagreement is not automatically a fault: a channel carried in one country and made in another is ordinary |
| **Load/Process Channels** | Load channel and stream data from the database |
| **Preview Changes** | Dry run with a CSV export |
| **Match & Assign Streams** | Fuzzy match and assign streams to channels |
| **Match US OTA Only** | Match US broadcast channels by callsign |
| **Sort Alternate Streams** | Re-sort existing streams by quality, and by throughput tier where probe data exists |
| **Probe Stream Throughput** | Measure sustained throughput for assigned streams. Run this before Sort Alternate Streams |
| **Manage Channel Visibility** | Enable or disable channels based on stream count |
| **View Check Progress** | Show the running operation's percentage and estimated time |
| **View Last Results** | Show a summary of the last completed operation |
| **Clear CSV Exports** | Delete plugin CSV files, skipping any written in the last 40 minutes |
| **Cleanup Orphaned Tasks** | Remove scheduled task entries left by an older version |
| **Report a Bug or Request a Feature** | Write a ready-to-paste report with your settings, secrets masked |

## Scheduling

1. Set **Scheduled Run Times** in 24-hour format, for example `0400,1600`. Times
   use Dispatcharr's global timezone; there is no plugin timezone setting
2. Choose which steps run: **Schedule: Match & Assign Streams**, on by default,
   and **Schedule: Sort Streams**, off by default. They are independent, so a
   schedule can sort only, match only, or both
3. Enable **CSV Export** if you want one
4. Click **Update Schedule**

The scheduler runs in a background thread and re-arms when the container
restarts. Across multiple worker processes, a shared on-disk claim makes sure
the job runs once per slot rather than once per worker.

**Event-driven alternative** (Dispatcharr v0.27+): **Auto-match after M3U
refresh** runs Match and Assign the moment a refresh finishes, instead of or as
well as fixed times. Because Dispatcharr fires that event once per M3U account,
runs are coalesced under a lock and a follow-up pass catches any account that
finishes mid-match. A multi-account refresh therefore produces one effective
match rather than one per account.

## CSV Reports

Preview and scheduled exports are saved to `/data/exports/` and include:

- Threshold recommendations, from **Preview Changes**
- Token mismatch analysis, from **Preview Changes**
- Per-channel match counts, from **Match & Assign Streams**
- A match type breakdown: exact, substring, fuzzy

These are not the files that get emailed. See
[Notifications](docs/notifications.md) for that distinction, which matters
because the exports contain your M3U source names.

## Versioning

This plugin uses calver, `1.MAJOR.DDDHHMM`, being the UTC day of year plus the
UTC time. Run `python3 Stream-Mapparr/bump_version.py` to bump `plugin.json` and
`plugin.py` together.

## Changelog

See [CHANGELOG.md](Stream-Mapparr/CHANGELOG.md) for full version history.

## Disclaimer

**Stream-Mapparr provides no television content of any kind.** It supplies no channels, no
playlists, no streams, no electronic programme guide data and no provider accounts, and it
contains no list of where to obtain any of those. It matches and orders stream entries that
already exist in **your** Dispatcharr installation, using bundled reference data: curated
per-country channel name lists, and a table of United States broadcast station callsigns
published by the Federal Communications Commission.

Almost everything the plugin does reads stream *names*, never the streams themselves. There is
one exception, and it is opt-in. **Probe Stream Throughput** connects to stream URLs already
configured in your Dispatcharr installation and reads a few seconds of data from each, in order
to measure how fast that source delivers. It measures only the volume and timing of the bytes.
It does not decode, record, store, restream or redistribute anything, and the data it reads is
discarded. Nothing is fetched unless you run that action or enable throughput sorting.

Emailed reports, if you enable them, are handed to the Newsflasharr plugin, which sends them to
destinations **you** configured.

**You are responsible for what you connect Dispatcharr to.** Whether a particular provider,
subscription, playlist or stream is lawful for you to use depends on your agreement with that
provider and on the law where you live. Use only sources you are authorised to use. Nothing in
this project is intended to enable, encourage or assist access to content you have no right to
access.

All product names, channel names, network names, callsigns, trademarks and registered trademarks
mentioned in this project, or appearing in its examples or bundled reference data, are the
property of their respective owners. This project is an independent, community-built plugin. It
is not affiliated with, endorsed by, or sponsored by any television network, broadcaster,
streaming service or IPTV provider, and it is not affiliated with the Dispatcharr project beyond
being a plugin written for it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to set up, run the tests, and what
runs on a pull request.

## Sponsor

This plugin is free and always will be. If it saves you time and you would like
to support the work, you can sponsor it at
[github.com/sponsors/PiratesIRC](https://github.com/sponsors/PiratesIRC).

Sponsoring buys no priority, no private support and no influence over what gets
built. Bug reports and pull requests are just as welcome from everyone.

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Please do not include provider
credentials, stream URLs or M3U account names in a public issue.

## License

MIT
