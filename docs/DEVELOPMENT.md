# Stream-Mapparr — Development Workflow

This is the single source of truth for how to develop, test, and ship
Stream-Mapparr. It replaces the ad-hoc "manual testing" notes that used to live
only in `CLAUDE.md`.

Stream-Mapparr is a Python plugin that runs **inside Dispatcharr's Django
backend**. There is no standalone app to launch — the plugin is loaded by
Dispatcharr, talks to its ORM directly, and is distributed straight from this
repo to the Plugin Hub. That shapes everything below: our safety net has to live
in unit tests and CI because there is no staging environment between a commit and
a user's container.

---

## 1. One-time setup

```bash
# from the repo root
python -m venv .venv && . .venv/Scripts/activate   # Windows; use bin/activate on Unix
pip install -r requirements-dev.txt

# enable the pre-commit gate (compile + version sync + db validate + fast tests)
git config core.hooksPath .githooks
```

> **Why no Django in requirements?** Django, `pytz`, and the Dispatcharr ORM
> (`apps.channels.models`, `core.utils`) are provided by the Dispatcharr runtime.
> The test suite stubs them (see `tests/conftest.py`) so `plugin.py` can be
> imported in isolation. `rapidfuzz` is optional at runtime but **required for
> tests** so CI can exercise both matching code paths.

---

## 2. The inner loop

```
edit code  ->  pytest  ->  deploy to container  ->  watch logs
```

### Run the tests

```bash
python -m pytest -q                 # everything (~0.5s)
python -m pytest tests/test_fuzzy_matcher.py -q     # just the matcher
python -m pytest -k probe_fresh -q                  # one behavior
```

### Verify before deploy

```bash
python -m py_compile Stream-Mapparr/plugin.py Stream-Mapparr/fuzzy_matcher.py
python scripts/check_version_sync.py
python scripts/validate_databases.py
```

### Deploy to Dispatcharr

Use the **`/deploy-plugin`** skill, or do it by hand. The critical rule:

> **Copying files is not enough — you must restart.** The documented
> "hot-reload on `plugin.json` mtime" did NOT fire reliably in practice
> (verified 2026-06-13: three mtime-bumped deploys kept running the stale
> in-memory module for an hour). Bump the version, copy **every** changed file
> (incl. new modules like `aliases.py` and changed `fuzzy_matcher.py`), then
> `docker restart dispatcharr` and confirm the logged version.

```bash
python Stream-Mapparr/bump_version.py
for f in plugin.py plugin.json fuzzy_matcher.py aliases.py networks.json; do
  MSYS_NO_PATHCONV=1 docker cp "Stream-Mapparr/$f" dispatcharr:/data/plugins/stream-mapparr/
done
# copy any changed *_channels.json too. networks.json (FCC OTA station table) is
# easy to forget — the container/registry can be missing it entirely, which
# silently disables OTA callsign matching (bug-063).
docker restart dispatcharr
until docker logs --since 2m dispatcharr 2>&1 | grep -q "stream_mapparr.*initialized"; do sleep 2; done
docker logs --since 3m dispatcharr 2>&1 | grep -i "stream_mapparr.*initialized"  # version MUST match the bump
```

Notes:
- A background thread started under the old code keeps running the old code until
  it exits — fresh deploys only affect new invocations (another reason to restart).
- After the backend reloads, the settings UI may still show a cached form
  (e.g. a removed field). Hard-refresh the Dispatcharr plugins page.
- `MSYS_NO_PATHCONV=1` stops Git Bash mangling the `:/data/...` container path on Windows.

---

## 3. The test suite

Located in `tests/`. Most cases are **regression locks derived from real bugs**
(`.wolf/buglog.json`, the `BUG_REPORT_*.md` files). When you fix a bug, add the
case that would have caught it.

| File | Covers | Needs Django stub? |
|---|---|---|
| `test_fuzzy_matcher.py` | normalization, similarity, callsign extraction, the numeric-sibling guard (bug-021), zone expansion, bare-timezone over-strip (bug-066) | No — pure stdlib + rapidfuzz |
| `test_plugin_helpers.py` | `_parse_tags`, `_parse_priority_list`, `_audio_rank`, `_is_probe_fresh` (bug-008), scheduler `__init__` bootstrap (bug-065) | Yes (via conftest) |
| `test_progress_notifications.py` | `format_eta`, Discord/Slack webhook reshaping (bug-067), progress/last-results round-trips, View Progress/Last Results, live-toast wiring | Yes (via conftest) |
| `test_databases.py` | schema of every `*_channels.json` (the US file is 3.6 MB) | No |
| `test_version_sync.py` | `plugin.json` version == `PLUGIN_VERSION`, calver format | No |

The suite is **243 tests** as of v1.26.1761110.

### How importing `plugin.py` works in tests

`plugin.py` imports Django and the Dispatcharr ORM at module load. `conftest.py`
installs lightweight stubs into `sys.modules` (a fake `django.utils.timezone.now`,
`MagicMock` ORM models, etc.) and loads the plugin as a package submodule so its
relative imports (`from .fuzzy_matcher import FuzzyMatcher`) resolve. You never
need a configured Django to run the suite.

Test only **pure** helpers this way (no DB/HTTP/ORM side effects). For instance
methods that need no instance state, `Plugin.__new__(Plugin)` gives you a bare
object without running `__init__`.

### Matcher path parity — `bug-026` (fixed 2026-06-10)

`calculate_similarity` has two implementations: a rapidfuzz fast path and a
pure-Python fallback. They previously used *different* normalization formulas, so
results depended on whether `rapidfuzz` was installed — at threshold 95,
"Fox Sports 1" vs "Fox Sports 2" scored 0.917 (rapidfuzz) vs 0.958 (pure-Python),
flipping the match decision.

Both now return `1 - distance / max(len)`. Production runs the rapidfuzz path, so
live behavior didn't change — only the fallback was corrected to agree.
`test_rapidfuzz_and_pure_python_agree` enforces this going forward; if you ever
touch the similarity math, that test must stay green.

> Note: when `calculate_similarity` is called *with* a `threshold`, the two paths
> can still return different numeric values for scores **below** the cutoff
> (rapidfuzz zeroes them; the fallback may return the low score). That's harmless
> — every caller compares `>= threshold`, so match decisions and all kept scores
> are identical. The parity test deliberately checks the unthresholded score.

---

## 4. Continuous integration

`.github/workflows/ci.yml` runs on every push to `main`, every PR, and manual
dispatch. It is the only gate between a commit and a user pulling broken code.

Steps: install dev deps → byte-compile both modules → version sync → database
validation → `pytest`. Byte-compilation works without Django because compiling
does not execute imports.

The pre-commit hook (`.githooks/pre-commit`) runs a fast subset locally so most
failures are caught before they ever reach CI.

---

## 5. Releasing

Use the **`/release`** skill for the full checklist. Summary of the house style:

- **Version scheme:** calver `1.MAJOR.DDDHHMM` (UTC day-of-year + HHMM). Always
  bump with `Stream-Mapparr/bump_version.py` so `plugin.py` and `plugin.json`
  stay in lockstep.
- **Release tag:** bare calver, **no `v` prefix** (e.g. `1.26.1611423`).
- **End-to-end means end-to-end:** push, tagged GitHub release with the zip
  attached, comment + close the resolved issues, update **all** docs
  (`CHANGELOG.md`, `README.md`, `CLAUDE.md` current-version line), and deploy to
  the container. Stale version strings in docs are a recurring bug class — grep
  for the old version before you finish.
- **Validate the zip before attaching it:** `python scripts/validate_zip.py
  Stream-Mapparr.zip` must print OK (see the packaging note in §5 tooling — bug-087).

### Marketplace (Dispatcharr Plugin Hub)

The Hub is the **Dispatcharr/Plugins** repo; the fork lives at `Dispatcharr-Plugins-Fork`
(`origin` = PiratesIRC/Plugins, `upstream` = Dispatcharr/Plugins). Stream-Mapparr now uses
the **external `source_url` format** (per the Hub `CONTRIBUTING.md`): `plugins/stream-mapparr/`
holds only a slim `plugin.json` (`source_type: "external"`, `source_url` with a `{version}`
placeholder pointing at the GitHub release zip) plus `logo.png`. The registry downloads,
scans (CodeQL + ClamAV), re-hosts, and GPG-signs the release zip on each version bump. To
publish a new version: branch fresh off `upstream/main` (do not disturb other in-progress
branches), bump `version` in the manifest, push to the fork, and open a PR to
`Dispatcharr/Plugins` titled `[stream-mapparr] …`. Validation enforces the version increment
and that `source_url` resolves. The `{version}` substitutes the bare-calver tag, so the
`source_url` is `…/releases/download/{version}/Stream-Mapparr.zip` (no `v`).

> **bug-087 caution:** because the registry re-hosts the release zip and only re-fetches on a
> version **bump** (it forbids re-submitting the same version), a broken release zip cannot be
> fixed in place — replacing the asset does not update the marketplace. The fix is to bump,
> re-release a corrected zip, and open a new manifest-bump PR. So a bad zip propagates to the
> Hub; `scripts/validate_zip.py` before upload is the cheap insurance.

### Tooling notes (this environment)

- **`gh` is installed** (via winget; authenticated as PiratesIRC). If `gh` isn't on PATH in
  a fresh shell, it lives at
  `…\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\gh.exe`.
  GitHub Releases, issue comments/close, and Hub PRs are done with `gh` directly.
- **`docker cp` and stdin-piped `docker exec` trip the shell harness here.** Deploy by
  staging files into the `/config` bind mount (host `O:\docker\dispatcharr\config`) then
  `docker exec dispatcharr cp /config/<file> /data/plugins/stream-mapparr/<file>` (plain
  `docker exec` works).
- **Build the release zip with 7-Zip (`zip.cmd`) or `git archive` — NEVER PowerShell
  `Compress-Archive` or .NET Framework `ZipFile.CreateFromDirectory` (bug-087).** Those
  write ZIP entry paths with backslash (`\`) separators, which violate the ZIP spec; on
  Dispatcharr's Linux host the backslash is a literal filename char, so the package
  extracts as flat files (`Stream-Mapparr\plugin.py`) and install fails with "missing
  plugin.py or package __init__.py". The legacy `zip.cmd` has a different user's hardcoded
  paths — fix the paths or build with `git archive`, then **always** gate the artifact with
  `python scripts/validate_zip.py Stream-Mapparr.zip` before upload (it parses raw
  central-directory bytes; `zipfile.namelist()` normalizes backslashes away and cannot see
  the defect). On Windows, a reliable forward-slash build is .NET `ZipArchive.CreateEntry`
  with explicit `"Stream-Mapparr/"+name` entry names.

---

## 6. Conventions & gotchas (the short list)

- **`_parse_tags` is the canonical comma-list parser** (quote-aware). New
  comma-separated settings should delegate to it, not hand-roll `split(',')`.
- **Dispatcharr rejects blank field option values** — a dynamic option with
  `{"value": ""}` makes the serializer silently drop the *entire* field. Use a
  non-blank sentinel (`_none`) and normalize it back to `""` at every read site
  (bug-020).
- **`M3UAccount.user_agent` is a ForeignKey**, not a string — dig into
  `.user_agent / .value / .string / .name` (bug-007).
- **Failed throughput probes (`throughput_mbps is None`) are never "fresh"** —
  otherwise a bad run locks out re-probing for a TTL window (bug-008).
- **Don't use `django.utils.timezone.utc`** (removed in Django 5.0). Use
  `timezone.now()` or the stdlib `dt_timezone.utc` alias (bug-006).
- **The background scheduler bootstraps from `Plugin.__init__`, not `on_load`**
  (bug-065). This Dispatcharr build never calls `on_load` but re-instantiates the
  Plugin constantly, so arming only from `on_load` left the scheduler permanently
  dead. `__init__ → _load_settings → _start_background_scheduler`, serialized by a
  re-entrant `_scheduler_lock` (concurrent construction otherwise orphans a
  scheduler thread and double-fires scans). The scheduler reads
  `stream_mapparr_settings.json`, which can drift from the DB
  (`PluginConfig.settings`) — sync the file when changing schedule outside the UI.
- **`_send_progress_update` is the single progress hub** — it persists
  `stream_mapparr_progress.json` / `stream_mapparr_last_results.json` and drives
  live toasts for *all* long-running actions. Don't add a parallel progress path;
  route through it. `load_process_channels` is in `_INTERNAL_PROGRESS_ACTIONS`
  (suppressed) so internal sub-steps don't clobber the parent op's state.
- **The scheduled run is deduped across workers, not per-process** (bug-069). Each
  worker arms its own scheduler; `_claim_scheduled_slot` (shared last-run file +
  `fcntl.flock`) makes the run fire once per slot. `fcntl` is `None` on Windows, so
  the tests exercise the plain check-and-stamp path; the real flock path is
  verified by running the claim in the Linux container.
- **Zone routing is per-channel, gated by `_zone_routed_map`** (bug-068). It only
  fires when a zone-stripped base name has a different-zone sibling, so lone
  channels never change. Route every per-channel order through
  `_streams_for_channel` (Match & Assign, Sort, Preview all share it) — do not
  re-derive zone affinity inline. The matcher copies have **diverged** on regional
  stripping: bug-066 (drop bare Pacific/Central/Mountain/Atlantic) is correct for
  the channel matchers (Stream-Mapparr, Channel-Maparr) but breaks EPG-Janitor /
  Lineuparr (they intentionally strip bare Pacific). Run each plugin's suite before
  porting a matcher change.
- **Channel-database `type` contract:** only a `type` containing the substring
  `"broadcast"` is semantically significant — it marks an OTA channel and
  REQUIRES a `callsign` (matched by callsign). Every other `type` string
  (`premium/cable/national`, `National`, `Regional`, `International`, …) is
  free-form and name-matched; the specific value is not interpreted.
  `scripts/validate_databases.py` enforces exactly this (broadcast → needs
  `callsign`; else → needs `channel_name`). Do not reject a contributed database
  for using non-canonical `type` strings.
- **OTA callsign matching is backed by `networks.json`, not `*_channels.json`**
  (bug-063). The `*_channels.json` databases ship only premium entries (no
  `broadcast`/`callsign`), so `FuzzyMatcher._load_broadcast_stations()` loads the
  bundled FCC station table `networks.json` (≈1,900 callsigns) into
  `channel_lookup`. `Plugin._resolve_ota_callsign()` resolves a channel's
  callsign against it (falling back to a parenthesized callsign in the name), and
  `_build_us_callsign_database` (Match US OTA Only) is sourced from it too.
  **Deploy `networks.json` with the code** — if it is missing, OTA matching is
  silently disabled.

---

## 7. Repo map for developers

```
Stream-Mapparr/
  plugin.py            # main plugin (~260 KB) — Plugin class, actions, ORM, scheduler, probe
  fuzzy_matcher.py     # matching library — no Django dependency, unit-test heaven
  bump_version.py      # version sync tool (run before every deploy/release)
  aliases.py           # built-in US channel-name alias table
  *_channels.json      # per-country channel databases (US is 3.6 MB)
  networks.json        # FCC OTA station table (callsign authority) — deploy it!
tests/                 # pytest suite (this doc, section 3)
scripts/
  check_version_sync.py    # CI/hook: plugin.json == plugin.py
  validate_databases.py    # CI/hook: channel DB schema
.github/workflows/ci.yml   # CI pipeline
.githooks/pre-commit       # local fast gate (opt in with core.hooksPath)
docs/DEVELOPMENT.md        # you are here
```

### Backlog / not yet done

- `plugin.py` at ~260 KB is past the size where edits are safe by eye. Once the
  test suite has grown, split the probe / scheduler / sort concerns into modules.
- The ~50 historical `CHANGELOG_v*.md` / `QUICK_SUMMARY_*.md` / `BUG_REPORT_*.md`
  files duplicate the consolidated `Stream-Mapparr/CHANGELOG.md`; archive them
  under `docs/history/` and stop generating per-version files.
