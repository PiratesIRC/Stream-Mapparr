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
python -m py_compile Stream-Mapparr/plugin.py Stream-Mapparr/fuzzy_matcher.py Stream-Mapparr/matching_core.py
python scripts/check_version_sync.py
python scripts/validate_databases.py
```

### Deploy to Dispatcharr

Use the **`/deploy-plugin`** skill, or do it by hand. The critical rule:

> **Copying files is not enough — you must restart.** The documented
> "hot-reload on `plugin.json` mtime" did NOT fire reliably in practice
> (verified 2026-06-13: three mtime-bumped deploys kept running the stale
> in-memory module for an hour). Bump the version, copy **every** changed file
> (incl. the vendored shared core `matching_core.py` — `fuzzy_matcher.py` imports
> `FuzzyMatcherCore` from it, so the plugin won't load without it — plus new modules
> like `aliases.py` and changed `fuzzy_matcher.py`), then
> `docker restart dispatcharr` and confirm the logged version.

```bash
python Stream-Mapparr/bump_version.py
for f in plugin.py plugin.json fuzzy_matcher.py matching_core.py aliases.py networks.json; do
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
| `test_core_parity.py` | shared-core drift gate: sha256 of the vendored `matching_core.py` == `scripts/core_manifest.json` | No |
| `test_matcher_golden.py` | matcher golden drift gate: pure primitives vs `tests/matcher_golden_baseline.json` | No — pure stdlib + rapidfuzz |
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

> Note: the early-termination parameter is now `min_ratio` (the shared core replaced
> rapidfuzz's `score_cutoff` with a Python `>= min_ratio` gate applied identically on both
> paths). A score landing exactly on the cutoff is **kept** (`>=`, inclusive) and a
> below-cutoff score is zeroed on both paths, so match decisions and all kept scores are
> identical regardless of whether rapidfuzz is installed. The parity test deliberately
> checks the unthresholded (`min_ratio=0`) score.

> **Superseded by the shared-core migration (2026-06-28):** there are no longer separate
> drifting matcher copies to reconcile. `calculate_similarity` (and every other pure
> primitive) now lives once in the vendored shared core `matching_core.py`; this plugin's
> `FuzzyMatcher` subclasses `FuzzyMatcherCore`. A similarity change is made in
> `_shared/matching_core.py` and re-vendored with `scripts/sync_core.py` — never
> hand-ported across plugins. The canonical `1 - distance / max(len)` formula (rapidfuzz
> `Levenshtein.normalized_similarity`) is what landed in the core. See **Shared matcher
> core** below.

### Normalization ports from Lineuparr PR #13 (landed 2026-06-25)

Three normalization hardening fixes were ported from Lineuparr into the matcher
(`calculate_similarity` untouched — see the reconciliation note above). All are
regression-locked in `tests/test_fuzzy_matcher.py`; full suite green at **243 passed**.
_These primitives now live in the shared core_ — post the 2026-06-28 migration the bullets
below describe `matching_core.py`, inherited via the `FuzzyMatcher(FuzzyMatcherCore)`
subclass, not a private copy.

- **Non-ASCII preservation in `process_string_for_matching`.** After the NFKD fold the
  builder now keeps any `char.isalnum()` (alphanumerics of any script) instead of the
  old ASCII-only `a-z0-9` filter. Cyrillic / CJK / Arabic names previously collapsed to
  `''`, which compared as a false 100% match against any other all-non-ASCII name.
- **Leading box-bar bouquet-tag strip in `normalize_name`.** `_LEADING_BAR_TAG_RE` removes
  a leading `┃…┃` / `│…│` tag (`"┃CANAL+┃ NPO 1"` → `"NPO 1"`).
- **Zero-width / invisible format-char strip in `normalize_name` (bug-105, issue #36).**
  The first thing `normalize_name` now does is drop every Unicode category-`Cf` character
  (`name = ''.join(c for c in name if unicodedata.category(c) != 'Cf')`): ZERO WIDTH SPACE
  `U+200B`, ZWNJ/ZWJ, WORD JOINER `U+2060`, BOM `U+FEFF`, SOFT HYPHEN, bidi marks. These are
  invisible padding some providers wrap around a decorative block glyph (`"UK ␣▎␣BBC 1 FHD"`).
  The visible glyph (`▎`, category `So`) was already handled by the `_DECORATOR_CATS` pass,
  but the surrounding `Cf` chars are matched by neither `\s` nor `_DECORATOR_CATS`, so they
  survived the whole pipeline (`"​ ​BBC 1"`) and silently tanked the provider's match rate.
  Removed (not spaced) because they are zero-width: a ZWSP inside `BB<ZWSP>C` must yield
  `BBC`, not `BB C`. Non-ASCII letters (Cyrillic/CJK/Arabic, categories `Lu`/`Ll`/`Lo`) are
  untouched. Lives in the shared core, so the golden baseline was unaffected (corpus has no
  `Cf` chars) — no regen needed.
- **Box-bar delimiters in `GEOGRAPHIC_PATTERNS`.** `┃` (U+2503) and `│` (U+2502) accepted
  as colon-equivalents after a 2-3 letter code, and as matched pairs (`┃XX┃` / `│XX│`,
  matched pair only). Stream-Mapparr has no `PROVIDER_PREFIX_PATTERNS` list, so only
  `GEOGRAPHIC_PATTERNS` changed (this is where the matcher copies differ).

### Shared matcher core (migration landed 2026-06-28)

The era of a copy-pasted, drifting `fuzzy_matcher.py` is over. The pure matching
primitives — `normalize_name`, `calculate_similarity`, `process_string_for_matching`, the
callsign ladder, and the regex tables — now live **once** in the shared core
`<workspace>/_shared/matching_core.py`, vendored **byte-identically** into the inner folder
as `matching_core.py`. This plugin's `FuzzyMatcher` **subclasses `FuzzyMatcherCore`** and
keeps only its plugin-specific layer (zone routing, OTA callsign resolution, channel-DB
loading). Stream-Mapparr was the migration canary.

**To change matcher behavior** (do NOT hand-port to four copies anymore — that process is
retired):

1. Edit `_shared/matching_core.py`.
2. Re-vendor: `python scripts/sync_core.py` (copies the file into the inner folder and
   rewrites `scripts/core_manifest.json` with its sha256). `--check` verifies the vendored
   copy is byte-identical to `_shared` (runs locally / in the pre-commit hook, since CI has
   no `_shared`); `--dry-run` previews.
3. If behavior changed, regenerate the golden baseline (`tests/matcher_golden_baseline.json`).
4. Commit. Keep `matching_core.py` **out** of `bump_version.py`'s version stamping, or the
   hash gate fails forever.

Guards: the vendored core is **hash-pinned** (`scripts/core_manifest.json`) and enforced by
`tests/test_core_parity.py` (sha256 of the inner copy == manifest) plus the golden gate
(`tests/test_matcher_golden.py`); `.gitattributes` pins `matching_core.py` to LF so a CRLF
checkout can't change the hash.

Two core improvements landed with the migration: a **`strip_bare_region` opt-in** (a subclass
sets `_STRIP_BARE_REGION = True` to strip bare time-zone region words for scoring;
Stream-Mapparr leaves it off, preserving the bug-066 behavior) and the
**`calculate_similarity` `>= min_ratio` Python gate** that replaced rapidfuzz's
`score_cutoff` (so both code paths gate identically — see the parity note above).

---

## 4. Continuous integration

`.github/workflows/ci.yml` runs on every push to `main`, every PR, and manual
dispatch. It is the only gate between a commit and a user pulling broken code.

Steps: install dev deps → byte-compile the sources (`plugin.py`, `fuzzy_matcher.py`,
`matching_core.py`, `__init__.py`) → version sync → database validation → matcher golden
drift gate (`tests/test_matcher_golden.py`) → `pytest` (which includes the shared-core
hash-pin parity gate `tests/test_core_parity.py`). Byte-compilation works without Django
because compiling does not execute imports.

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
- **Auto-match on M3U refresh is event-driven, not a thread** (v1.26.1861801+). The
  hidden `on_m3u_refresh` action declares `"events": ["m3u_refresh"]`, and Dispatcharr
  (v0.27+) dispatches it once **per M3U account** with `params = {"event", "payload"}`
  — the real config is in `context["settings"]`, NOT the 2nd `run()` arg (which holds
  only the event dict on this path). The handler reads `context["settings"]`, gates on
  `_should_auto_match_on_refresh` (opt-in bool + a selected profile, a pure string
  check — no ORM), then runs `add_streams_to_channels_action` **synchronously** (the
  event fires inside a Celery worker; a daemon thread could be reaped mid-run) while
  holding a cross-worker `fcntl.flock` (`_acquire_m3u_refresh_flock`, same stale-break
  + Windows-degrade pattern as bug-069). Concurrent per-account events that lose the
  flock drop a dirty-flag marker (`stream_mapparr_m3u_refresh_pending`); the flock
  holder reruns once more if the marker was set during a pass, so late accounts aren't
  dropped (loop always terminates — events are finite). Registration gotcha: the action
  must live in the **`plugin.py` `actions` list** (the manifest `actions` are ignored
  for an enabled plugin) and MUST carry a `label` or Dispatcharr's `_normalize_actions`
  silently drops it. `"events"` is inert on Dispatcharr < 0.27, so `min_dispatcharr_version`
  stays `v0.20.0`.
- **Zone routing is per-channel, gated by `_zone_routed_map`** (bug-068). It only
  fires when a zone-stripped base name has a different-zone sibling, so lone
  channels never change. Route every per-channel order through
  `_streams_for_channel` (Match & Assign, Sort, Preview all share it) — do not
  re-derive zone affinity inline. Regional stripping is now a **shared-core opt-in**, not a
  per-copy divergence: bug-066 (drop bare Pacific/Central/Mountain/Atlantic) is the default
  in `matching_core.py`, correct for the channel matchers (Stream-Mapparr, Channel-Maparr);
  EPG-Janitor / Lineuparr opt back into stripping bare Pacific by setting
  `_STRIP_BARE_REGION = True` on their subclass. Stream-Mapparr leaves it off. A matcher
  change goes to the shared core (see "Shared matcher core" in §3), not a hand-port — but
  still run the suite, since the golden + parity gates lock the behavior.
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
  matching_core.py     # VENDORED shared core (FuzzyMatcherCore) — byte-identical to
                       #   <workspace>/_shared/matching_core.py; hash-pinned, LF-locked; deploy it!
  fuzzy_matcher.py     # plugin matcher layer — FuzzyMatcher(FuzzyMatcherCore); no Django dependency
  bump_version.py      # version sync tool (run before every deploy/release; does NOT stamp matching_core.py)
  aliases.py           # built-in US channel-name alias table
  *_channels.json      # per-country channel databases (US is 3.6 MB)
  networks.json        # FCC OTA station table (callsign authority) — deploy it!
tests/                 # pytest suite (this doc, section 3)
  test_core_parity.py        # vendored core sha256 == core_manifest.json
  test_matcher_golden.py     # pure primitives vs matcher_golden_baseline.json
scripts/
  check_version_sync.py    # CI/hook: plugin.json == plugin.py
  validate_databases.py    # CI/hook: channel DB schema
  sync_core.py             # re-vendor _shared/matching_core.py + rewrite core_manifest.json
  core_manifest.json       # sha256 pin of the vendored shared core
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
