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

> **Dispatcharr hot-reloads on `plugin.json` mtime, NOT `plugin.py`.**
> Always bump the version (which touches `plugin.json`) and copy **both** files,
> or the old module stays in memory and you debug a build that isn't running.

```bash
python Stream-Mapparr/bump_version.py
docker cp Stream-Mapparr/plugin.py   dispatcharr:/data/plugins/stream-mapparr/
docker cp Stream-Mapparr/plugin.json dispatcharr:/data/plugins/stream-mapparr/
docker logs --tail 50 dispatcharr | grep -i stream-mapparr   # confirm new version is live
```

A background thread started under the old code keeps running the old code until
it exits — fresh deploys only affect new invocations.

---

## 3. The test suite

Located in `tests/`. Most cases are **regression locks derived from real bugs**
(`.wolf/buglog.json`, the `BUG_REPORT_*.md` files). When you fix a bug, add the
case that would have caught it.

| File | Covers | Needs Django stub? |
|---|---|---|
| `test_fuzzy_matcher.py` | normalization, similarity, callsign extraction, the numeric-sibling guard (bug-021), zone expansion | No — pure stdlib + rapidfuzz |
| `test_plugin_helpers.py` | `_parse_tags`, `_parse_priority_list`, `_audio_rank`, `_is_probe_fresh` (bug-008) | Yes (via conftest) |
| `test_databases.py` | schema of every `*_channels.json` (the US file is 3.6 MB) | No |
| `test_version_sync.py` | `plugin.json` version == `PLUGIN_VERSION`, calver format | No |

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

---

## 7. Repo map for developers

```
Stream-Mapparr/
  plugin.py            # main plugin (~260 KB) — Plugin class, actions, ORM, scheduler, probe
  fuzzy_matcher.py     # matching library — no Django dependency, unit-test heaven
  bump_version.py      # version sync tool (run before every deploy/release)
  *_channels.json      # per-country channel databases (US is 3.6 MB)
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
