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

The suite is **226 tests** as of v1.26.1742332.

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
