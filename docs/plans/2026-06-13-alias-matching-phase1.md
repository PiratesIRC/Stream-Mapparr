# Phase-1 US Alias Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an SM-native, exact-normalized alias layer to Stream-Mapparr so abbreviation/rebrand channel names (FS1→"Fox Sports 1", WGN America→"NewsNation") resolve to streams fuzzy matching structurally misses, plus reconcile the documented `type` taxonomy with live behavior.

**Architecture:** A new pure `FuzzyMatcher.alias_lookup()` returns streams whose normalized name exactly equals a curated alias variant of the channel name. A `Plugin._collect_alias_streams()` helper force-includes those streams into the result set at both matching call sites — bypassing the channel-name re-match filter that would otherwise drop alias-only hits. An alias table (`aliases.py`) and a user `custom_aliases` setting feed the map. Exact-normalized only: no fuzzy-alias, no Lineuparr helper dependencies, no threshold retuning, inherently numeric-sibling-safe.

**Tech Stack:** Python 3.12, pytest, rapidfuzz (optional, runtime), Django/ORM (stubbed in tests via `tests/conftest.py`).

**Spec:** `docs/specs/2026-06-13-alias-matching-phase1-design.md`

---

## File Structure

- **Create** `Stream-Mapparr/aliases.py` — `CHANNEL_ALIASES` (US table) + `COUNTRY_ALIASES` (scaffold).
- **Modify** `Stream-Mapparr/fuzzy_matcher.py` — add `alias_lookup()`; bump `__version__`.
- **Modify** `Stream-Mapparr/plugin.py` — import aliases; `__init__` adds `self._alias_map=None`; add `_build_alias_map()` + `_collect_alias_streams()`; build map after the 2 matcher-init sites; force-include in `_match_streams_to_channel` and `_get_matches_at_thresholds`; add `custom_aliases` field.
- **Create** `tests/test_aliases.py` — table audit (bare-token guard).
- **Create** `tests/test_alias_matching.py` — `alias_lookup`, `_build_alias_map`, `_collect_alias_streams`, the critical force-include regression.
- **Modify** `CLAUDE.md` (project + workspace-root reference) — taxonomy contract.

---

## Task 0: Taxonomy reconciliation (docs only)

**Files:**
- Modify: `C:\Users\User\docker\Stream-Mapparr\CLAUDE.md`
- Modify: `C:\Users\User\docker\CLAUDE.md` (workspace root)

- [ ] **Step 1: Update the project CLAUDE.md channel-type contract**

In `Stream-Mapparr/CLAUDE.md`, find the "Adding Channel Databases" section and the channel-type description. Replace the implication that `type` must be `"broadcast (OTA)"` or `"premium/cable/national"` with the real contract. Add this sentence to the section:

```markdown
**Type contract:** Only a `type` containing the substring `"broadcast"` is
semantically significant — it marks an OTA channel and REQUIRES a `callsign`
(matched by callsign). Every other `type` string (e.g. `premium/cable/national`,
`National`, `Regional`, `International`) is free-form and is name-matched; the
specific value is not interpreted. `scripts/validate_databases.py` enforces
exactly this (broadcast → needs callsign; else → needs channel_name).
```

- [ ] **Step 2: Update the workspace-root CLAUDE.md if it restates the taxonomy**

Run: `grep -n "premium/cable/national\|broadcast (OTA)" C:/Users/User/docker/CLAUDE.md`
If any line restates the strict two-value taxonomy, append a clause: `(only a "broadcast" type is special; other type strings are free-form, name-matched)`. If grep returns nothing, skip this step.

- [ ] **Step 3: Update the PR #30 review comment**

Run:
```bash
export GH_TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2-)
gh pr comment 30 --repo PiratesIRC/Stream-Mapparr --body "Update after review of the live databases: I'm retracting the type-normalization request. The matcher and validate_databases.py only treat a \`type\` containing \"broadcast\" as special (OTA, needs callsign); every other value (including National/Regional/International) is free-form and name-matched — and the live US_channels.json already uses National/Regional. So Norway's types are fine as-is. The only remaining ask is the docs note you already flagged: please add \`NO\` to the channel-database list. Thanks again!"
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: clarify channel type contract (only 'broadcast' is special)"
```

---

## Task 1: Create the US alias table

**Files:**
- Create: `Stream-Mapparr/aliases.py`
- Source to copy from: `C:\Users\User\docker\Lineuparr\Lineuparr\aliases.py`
- Test: `tests/test_aliases.py`

- [ ] **Step 1: Write the failing table-audit test**

Create `tests/test_aliases.py`:

```python
"""Audit the alias table for the bare-short-token guard (Lineuparr lesson).

A bare short token as an alias VALUE (e.g. "UP", "GET", "GAC", "great")
normalizes to a 2-3 char string that exact-matches unrelated short streams.
Such tokens are forbidden as values; the canonical name carries the alias.
"""
import importlib.util
import sys
from pathlib import Path

SM = Path(__file__).resolve().parents[1] / "Stream-Mapparr"


def _load_aliases():
    spec = importlib.util.spec_from_file_location("sm_aliases", SM / "aliases.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sm_aliases"] = mod
    spec.loader.exec_module(mod)
    return mod


FORBIDDEN_BARE = {"up", "get", "gac", "great"}


def test_no_forbidden_bare_token_values():
    mod = _load_aliases()
    offenders = []
    for key, variants in mod.CHANNEL_ALIASES.items():
        for v in variants:
            norm = "".join(ch for ch in v.lower() if ch.isalnum())
            if norm in FORBIDDEN_BARE:
                offenders.append((key, v))
    assert offenders == [], f"forbidden bare-token alias values: {offenders}"


def test_tables_are_dicts_of_string_lists():
    mod = _load_aliases()
    assert isinstance(mod.CHANNEL_ALIASES, dict)
    assert isinstance(mod.COUNTRY_ALIASES, dict)
    for key, variants in mod.CHANNEL_ALIASES.items():
        assert isinstance(key, str) and key
        assert isinstance(variants, list) and all(isinstance(v, str) for v in variants)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_aliases.py -q`
Expected: FAIL (`aliases.py` does not exist → import error).

- [ ] **Step 3: Create `Stream-Mapparr/aliases.py` (US subset)**

Copy the **US-relevant sections** of `C:\Users\User\docker\Lineuparr\Lineuparr\aliases.py` into a new `Stream-Mapparr/aliases.py`. Include the dict entries under these comment sections (Lineuparr line ranges given for reference): News, Sports, Movies, Kids, Entertainment, Home & Garden, Reality & Lifestyle, Comedy, Discovery, Crime, Music, Food & Travel, Premium channels, Additional aliases for DISH lineup, Faith & Family, Other, Faith (rebrands), Movies (rebrands/discontinued), and the `US rebrands` + `US: EPG guide-name bridges` sections (Lineuparr lines ~7–223 and ~368–433).

**EXCLUDE** the UK / FR / CA sections (Lineuparr lines ~225–367 and ~435–442) — those are Phase 2.

Then add the scaffold:

```python
# Country-scoped overrides (Phase 2). US-empty for now; the merge mechanism
# in Plugin._build_alias_map honors this dict per the selected channel_database.
COUNTRY_ALIASES = {}
```

File header:

```python
"""Built-in US channel alias table for Stream-Mapparr (Phase 1).

Maps canonical channel names (the matcher query) to known IPTV stream-name
variants. Used by FuzzyMatcher.alias_lookup for exact-normalized matching.

GUARDS (hard-won in the sibling Lineuparr plugin):
- Do NOT use bare short tokens (UP, GET, GAC, great) as alias VALUES — they
  normalize to 2-3 chars and exact-match unrelated short streams. The canonical
  name carries the alias instead.
- Do NOT catch-all regional feeds (e.g. FanDuel regionals) to a generic parent.
- Country-specific variants belong in COUNTRY_ALIASES (Phase 2), never here
  (cross-market leak, see Lineuparr bug-063).
"""
```

- [ ] **Step 4: Audit copied values against the bare-token guard**

Run: `python -m pytest tests/test_aliases.py -q`
Expected: PASS. If `test_no_forbidden_bare_token_values` fails, remove the offending bare value from that key's list (keep the multi-word / unambiguous variants) and re-run.

- [ ] **Step 5: Commit**

```bash
git add Stream-Mapparr/aliases.py tests/test_aliases.py
git commit -m "feat: add US channel alias table with bare-token guard test"
```

---

## Task 2: `alias_lookup` in the matcher

**Files:**
- Modify: `Stream-Mapparr/fuzzy_matcher.py` (add method to `FuzzyMatcher`; bump `__version__`)
- Test: `tests/test_alias_matching.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_alias_matching.py`:

```python
"""Tests for the exact-normalized alias layer (issue: abbreviation/rebrand lift)."""
import pytest


ALIAS_MAP = {
    "FS1": ["FS1", "Fox Sports 1", "FS 1"],
    "WGN America": ["WGN America", "WGN", "NewsNation"],
    "TCM": ["TCM", "Turner Classic Movies"],
    "UP": ["UPtv", "UP TV"],  # canonical 'UP' keyed; bare 'up' NOT a value
}


def test_alias_lookup_exact_normalized_hit(matcher):
    hits = matcher.alias_lookup("FS1", ["Fox Sports 1", "ESPN"], ALIAS_MAP)
    assert hits == ["Fox Sports 1"]


def test_alias_lookup_rebrand_hit(matcher):
    hits = matcher.alias_lookup("WGN America", ["NewsNation HD", "Random"], ALIAS_MAP)
    assert "NewsNation HD" in hits


def test_alias_lookup_numeric_sibling_safe(matcher):
    # FS1's variants never normalize to FS2 — exact matching can't cross siblings.
    hits = matcher.alias_lookup("FS1", ["FS2", "Fox Sports 2"], ALIAS_MAP)
    assert hits == []


def test_alias_lookup_no_entry_returns_empty(matcher):
    assert matcher.alias_lookup("Channel Not In Map", ["FS1"], ALIAS_MAP) == []


def test_alias_lookup_empty_map_returns_empty(matcher):
    assert matcher.alias_lookup("FS1", ["Fox Sports 1"], {}) == []
    assert matcher.alias_lookup("FS1", ["Fox Sports 1"], None) == []


def test_alias_lookup_nospace_match(matcher):
    # "Fox Sports 1" and "FoxSports1" normalize to the same nospace form.
    hits = matcher.alias_lookup("FS1", ["FoxSports1"], ALIAS_MAP)
    assert hits == ["FoxSports1"]
```

The `matcher` fixture already exists in `tests/conftest.py` (a `FuzzyMatcher` instance).

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_alias_matching.py -q`
Expected: FAIL (`AttributeError: 'FuzzyMatcher' object has no attribute 'alias_lookup'`).

- [ ] **Step 3: Implement `alias_lookup`**

In `Stream-Mapparr/fuzzy_matcher.py`, add this method to the `FuzzyMatcher` class (place it just before `fuzzy_match`, around line 830):

```python
    def alias_lookup(self, query_name, candidate_names, alias_map,
                     user_ignored_tags=None, ignore_quality=True, ignore_regional=True,
                     ignore_geographic=True, ignore_misc=True):
        """Exact-normalized alias match.

        Returns the list of candidate_names whose normalized form (spaced OR
        punctuation-stripped) exactly equals the normalized form of any alias
        variant of query_name. Pure; no fuzzy/similarity. Empty list when the
        map is empty or the channel has no alias entry.
        """
        if not alias_map or not candidate_names:
            return []
        variants = alias_map.get(query_name)
        if not variants:
            return []

        def _forms(s):
            n = self.normalize_name(
                s, user_ignored_tags, ignore_quality=ignore_quality,
                ignore_regional=ignore_regional, ignore_geographic=ignore_geographic,
                ignore_misc=ignore_misc)
            if not n:
                return None, None
            low = n.lower()
            return low, re.sub(r'[\s&\-]+', '', low)

        alias_low, alias_nospace = set(), set()
        for v in variants:
            low, nospace = _forms(v)
            if low:
                alias_low.add(low)
                alias_nospace.add(nospace)
        if not alias_low:
            return []

        hits = []
        for cand in candidate_names:
            low, nospace = _forms(cand)
            if low and (low in alias_low or nospace in alias_nospace):
                hits.append(cand)
        return hits
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_alias_matching.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Bump the matcher version**

In `Stream-Mapparr/fuzzy_matcher.py`, find `__version__` (line ~22) and bump the patch component (e.g. `"26.161.2350"` → `"26.164.XXXX"` using current UTC day-of-year + HHMM, or simply increment the last number). This is an internal marker; exact value is not load-bearing.

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/fuzzy_matcher.py tests/test_alias_matching.py
git commit -m "feat: add exact-normalized alias_lookup to FuzzyMatcher"
```

---

## Task 3: `_build_alias_map` in the plugin

**Files:**
- Modify: `Stream-Mapparr/plugin.py` (import; add method; `__init__`)
- Test: `tests/test_alias_matching.py` (append)

- [ ] **Step 1: Write the failing tests (append to `tests/test_alias_matching.py`)**

```python
def _bare_plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_build_alias_map_includes_builtin(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({}, None)
    assert "FS1" in m  # from the built-in US table


def test_build_alias_map_merges_custom_object(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '{"My Channel": ["My Stream"]}'}, None)
    assert m["My Channel"] == ["My Stream"]


def test_build_alias_map_custom_bare_string(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '{"X": "Y"}'}, None)
    assert m["X"] == ["Y"]


def test_build_alias_map_custom_wins_and_merges(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '{"FS1": ["Foxy 1"]}'}, None)
    assert "Foxy 1" in m["FS1"] and "Fox Sports 1" in m["FS1"]  # merged, deduped


def test_build_alias_map_malformed_json_ignored(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": "{not json"}, None)
    assert "FS1" in m  # falls back to built-in, no raise


def test_build_alias_map_non_object_ignored(plugin_module):
    p = _bare_plugin(plugin_module)
    m = p._build_alias_map({"custom_aliases": '["a", "b"]'}, None)
    assert "FS1" in m  # list is not a mapping → ignored
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_alias_matching.py -q -k build_alias_map`
Expected: FAIL (`AttributeError: ... '_build_alias_map'`).

- [ ] **Step 3: Add the import and `__init__` attribute**

In `Stream-Mapparr/plugin.py`, near the existing `from .fuzzy_matcher import FuzzyMatcher` (line 21), add:

```python
from .aliases import CHANNEL_ALIASES, COUNTRY_ALIASES
```

In `__init__`, next to `self.fuzzy_matcher = None` (line ~793), add:

```python
        self._alias_map = None
```

- [ ] **Step 4: Implement `_build_alias_map`**

Add this method to the `Plugin` class (place it just after `_initialize_fuzzy_matcher`, around line 1248):

```python
    def _build_alias_map(self, settings, country):
        """Merge built-in US aliases + COUNTRY_ALIASES[country] + custom_aliases.

        Custom user aliases (JSON object; a bare string is accepted as a single
        alias) are merged last and win. Malformed/invalid input is logged and
        ignored — never raised into the match loop.
        """
        alias_map = {k: list(v) for k, v in CHANNEL_ALIASES.items()}

        if country:
            for k, v in COUNTRY_ALIASES.get(str(country).upper(), {}).items():
                alias_map[k] = list(dict.fromkeys(alias_map.get(k, []) + list(v)))

        custom_str = (settings.get("custom_aliases") or "").strip()
        if custom_str:
            try:
                custom = json.loads(custom_str)
            except (json.JSONDecodeError, ValueError) as e:
                LOGGER.warning(f"[Stream-Mapparr] Failed to parse custom_aliases JSON: {e}")
                custom = None
            if isinstance(custom, dict):
                merged = 0
                for k, v in custom.items():
                    if isinstance(v, str):
                        aliases = [v]
                    elif isinstance(v, list):
                        aliases = v
                    else:
                        LOGGER.warning(
                            f"[Stream-Mapparr] custom_aliases: ignoring '{k}' - "
                            f"value must be a string or list")
                        continue
                    clean = [a.strip() for a in aliases if isinstance(a, str) and a.strip()]
                    if not clean:
                        continue
                    if k in alias_map:
                        alias_map[k] = list(dict.fromkeys(alias_map[k] + clean))
                    else:
                        alias_map[k] = clean
                    merged += 1
                LOGGER.info(f"[Stream-Mapparr] Merged {merged} custom alias entries")
            elif custom is not None:
                LOGGER.warning(
                    "[Stream-Mapparr] custom_aliases must be a JSON object - ignored")

        return alias_map
```

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_alias_matching.py -q -k build_alias_map`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/plugin.py tests/test_alias_matching.py
git commit -m "feat: add _build_alias_map (built-in + custom_aliases merge)"
```

---

## Task 4: `_collect_alias_streams` helper

**Files:**
- Modify: `Stream-Mapparr/plugin.py` (add method)
- Test: `tests/test_alias_matching.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def _plugin_with_matcher(plugin_module, fuzzy_module, alias_map):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(match_threshold=80)
    p._alias_map = alias_map
    return p


def test_collect_alias_streams_returns_matching_dicts(plugin_module, fuzzy_module):
    p = _plugin_with_matcher(plugin_module, fuzzy_module, {"FS1": ["FS1", "Fox Sports 1"]})
    streams = [{"name": "Fox Sports 1", "id": 1}, {"name": "ESPN", "id": 2}]
    out = p._collect_alias_streams("FS1", streams,
                                   ignore_tags=[], ignore_quality=True, ignore_regional=True,
                                   ignore_geographic=True, ignore_misc=True)
    assert [s["id"] for s in out] == [1]


def test_collect_alias_streams_empty_when_no_matcher(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None
    p._alias_map = {"FS1": ["Fox Sports 1"]}
    out = p._collect_alias_streams("FS1", [{"name": "Fox Sports 1"}],
                                   ignore_tags=[], ignore_quality=True, ignore_regional=True,
                                   ignore_geographic=True, ignore_misc=True)
    assert out == []
```

`fuzzy_module` is an existing fixture in `tests/conftest.py`.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_alias_matching.py -q -k collect_alias`
Expected: FAIL (`AttributeError: ... '_collect_alias_streams'`).

- [ ] **Step 3: Implement `_collect_alias_streams`**

Add to the `Plugin` class (just after `_build_alias_map`):

```python
    def _collect_alias_streams(self, channel_name, working_streams, ignore_tags,
                               ignore_quality, ignore_regional, ignore_geographic, ignore_misc):
        """Return stream dicts whose name exact-normalizes to an alias variant of
        channel_name. Empty when the matcher or alias map is unavailable."""
        alias_map = getattr(self, "_alias_map", None)
        if not self.fuzzy_matcher or not alias_map:
            return []
        stream_names = [s["name"] for s in working_streams]
        hit_names = set(self.fuzzy_matcher.alias_lookup(
            channel_name, stream_names, alias_map, ignore_tags,
            ignore_quality=ignore_quality, ignore_regional=ignore_regional,
            ignore_geographic=ignore_geographic, ignore_misc=ignore_misc))
        if not hit_names:
            return []
        return [s for s in working_streams if s["name"] in hit_names]
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_alias_matching.py -q -k collect_alias`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add Stream-Mapparr/plugin.py tests/test_alias_matching.py
git commit -m "feat: add _collect_alias_streams force-include helper"
```

---

## Task 5: Build the alias map after matcher init

**Files:**
- Modify: `Stream-Mapparr/plugin.py:2836`, `:4168` (after each `_initialize_fuzzy_matcher` call)

- [ ] **Step 1: Read both init sites for context**

Run: `grep -n "_initialize_fuzzy_matcher(match_threshold)" Stream-Mapparr/plugin.py`
Expected: lines 2836 and 4168. Read ~10 lines around each to confirm `settings` is in scope and find the `channel_database` value name.

Run: `grep -n "channel_database" Stream-Mapparr/plugin.py | head`

- [ ] **Step 2: Set `self._alias_map` after each init call**

After the `self._initialize_fuzzy_matcher(match_threshold)` line at **2836** and at **4168**, add (matching the local `settings` variable name in scope — adjust if it differs):

```python
            self._alias_map = self._build_alias_map(settings, settings.get("channel_database"))
```

If `settings` is not the in-scope variable name at a site, use whatever dict holds the resolved settings there (confirm in Step 1).

- [ ] **Step 3: Verify import compiles**

Run: `python -m py_compile Stream-Mapparr/plugin.py`
Expected: no output (success).

- [ ] **Step 4: Commit**

```bash
git add Stream-Mapparr/plugin.py
git commit -m "feat: build alias map after matcher init at both entry points"
```

---

## Task 6: Force-include in `_match_streams_to_channel` (critical regression)

**Files:**
- Modify: `Stream-Mapparr/plugin.py` (inside `_match_streams_to_channel`, the fuzzy block ~2268-2360)
- Test: `tests/test_alias_matching.py` (append)

- [ ] **Step 1: Write the failing critical regression test (append)**

```python
def test_alias_only_stream_survives_rematch_filter(plugin_module, fuzzy_module):
    """THE blocker lock: an alias-only stream (FS1 -> 'Fox Sports 1') has low
    channel-name similarity and would be dropped by the re-match loop. It must
    still appear in the final matched set via the alias force-include."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(match_threshold=80)
    p._alias_map = {"FS1": ["FS1", "Fox Sports 1"]}

    channel = {"name": "FS1", "id": 10}
    streams = [{"name": "Fox Sports 1", "id": 1, "m3u_account": 1}]

    result = p._match_streams_to_channel(channel, streams, logger=None)
    matched = result[0]  # (streams, cleaned_channel, cleaned_streams, reason, db)
    assert any(s["id"] == 1 for s in matched), "alias-only stream was dropped"
```

> NOTE for the implementer: confirm the return tuple shape of
> `_match_streams_to_channel` (it returns `sorted_streams, cleaned_channel_name,
> cleaned_stream_names, match_reason, database_used`). If a no-match path returns
> a different shape (e.g. `([], ...)`), adjust the assertion to read the streams
> list from index 0. Read lines 2203-2440 before implementing.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_alias_matching.py -q -k survives_rematch`
Expected: FAIL — the alias-only stream is not in the matched set (current code drops it).

- [ ] **Step 3: Force-include alias hits in the fuzzy block**

Read `_match_streams_to_channel` lines 2266-2360. The fuzzy block is gated by `if self.fuzzy_matcher:` then `if matched_stream_name:`. Modify so alias hits are collected and unioned into `matching_streams` regardless of the fuzzy result. Replace the structure starting at the `if self.fuzzy_matcher:` block (line ~2268) so it reads:

```python
        # Use fuzzy matching if available
        if self.fuzzy_matcher:
            stream_names = [stream['name'] for stream in working_streams]
            matched_stream_name, score, match_type = self.fuzzy_matcher.fuzzy_match(
                channel_name, stream_names, ignore_tags, remove_cinemax=channel_has_max,
                ignore_quality=ignore_quality, ignore_regional=ignore_regional,
                ignore_geographic=ignore_geographic, ignore_misc=ignore_misc
            )

            # Alias force-include: exact-normalized alias hits bypass the
            # channel-name re-match filter below (an alias-only stream has low
            # name similarity by design). Collected independently of the fuzzy
            # result so alias-only channels still resolve.
            alias_streams = self._collect_alias_streams(
                channel_name, working_streams, ignore_tags, ignore_quality,
                ignore_regional, ignore_geographic, ignore_misc)

            if matched_stream_name or alias_streams:
                matching_streams = list(alias_streams)
                alias_ids = {id(s) for s in alias_streams}

                cleaned_channel_for_matching = self._clean_channel_name(
                    channel_name, ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                )
```

Then, in the existing `for stream in working_streams:` re-match loop that follows, guard each `matching_streams.append(stream)` so it does not duplicate an already-included alias stream. The simplest robust approach: at the top of the loop body add:

```python
                for stream in working_streams:
                    if id(stream) in alias_ids:
                        continue  # already force-included via alias
```

Leave the rest of the loop and the subsequent `_sort_streams_by_quality` /
`_deduplicate_streams` / `return` unchanged. The `if matched_stream_name:` gate
becomes `if matched_stream_name or alias_streams:` (shown above) and the
`matching_streams = []` initialization becomes `matching_streams = list(alias_streams)`.

> Implementer: this is the one delicate edit. Make exactly these three changes
> inside the block — (a) compute `alias_streams`, (b) widen the gate to
> `or alias_streams`, (c) seed `matching_streams = list(alias_streams)` + skip
> those ids in the loop. Do not alter the sort/dedup/return tail.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_alias_matching.py -q -k survives_rematch`
Expected: PASS.

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: all green (existing 105 + the new alias tests).

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/plugin.py tests/test_alias_matching.py
git commit -m "feat: force-include alias hits in _match_streams_to_channel (issue #28-style lift)"
```

---

## Task 7: Force-include in `_get_matches_at_thresholds`

**Files:**
- Modify: `Stream-Mapparr/plugin.py` (inside `_get_matches_at_thresholds`, the per-threshold block ~2515-2552)
- Test: `tests/test_alias_matching.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_threshold_path_includes_alias_streams(plugin_module, fuzzy_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(match_threshold=80)
    p._alias_map = {"TCM": ["TCM", "Turner Classic Movies"]}

    channel = {"name": "TCM", "id": 11}
    streams = [{"name": "Turner Classic Movies", "id": 5, "m3u_account": 1}]

    results = p._get_matches_at_thresholds(
        channel, streams, logger=None, ignore_tags=[], ignore_quality=True,
        ignore_regional=True, ignore_geographic=True, ignore_misc=True,
        channels_data=[], current_threshold=80)
    found = any(
        any(s["id"] == 5 for s in v.get("streams", []))
        for v in results.values())
    assert found, "alias-only stream missing from threshold results"
```

> Implementer: confirm `_get_matches_at_thresholds` param order from the signature
> at line 2451 before finalizing the call.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_alias_matching.py -q -k threshold_path`
Expected: FAIL (alias-only stream dropped by the exact-equality re-filter at 2545).

- [ ] **Step 3: Force-include alias hits in the threshold block**

Read lines 2515-2552. Inside the `for threshold in thresholds_to_test:` loop, after the `fuzzy_match` call (line 2517) and before the `if matched_stream_name:` gate (2523), add:

```python
                alias_streams = self._collect_alias_streams(
                    channel_name, candidate_streams, ignore_tags, ignore_quality,
                    ignore_regional, ignore_geographic, ignore_misc)
```

Change the gate `if matched_stream_name:` (2523) to `if matched_stream_name or alias_streams:`, seed `matching_streams = list(alias_streams)` instead of `matching_streams = []` (2533), and skip already-included alias streams in the re-match loop by adding at the top of `for stream in candidate_streams:` (2534):

```python
                        if any(s is stream for s in alias_streams):
                            continue
```

Leave the sort/dedup/results-assignment tail unchanged.

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_alias_matching.py -q -k threshold_path`
Expected: PASS.

- [ ] **Step 5: Full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add Stream-Mapparr/plugin.py tests/test_alias_matching.py
git commit -m "feat: force-include alias hits in _get_matches_at_thresholds"
```

---

## Task 8: Add the `custom_aliases` setting

**Files:**
- Modify: `Stream-Mapparr/plugin.py` (the `fields` list and `PluginConfig` default)

- [ ] **Step 1: Add a default constant**

In the `PluginConfig` class (near other `DEFAULT_*` string settings), add:

```python
    DEFAULT_CUSTOM_ALIASES = ""
```

- [ ] **Step 2: Add the field to the `fields` list**

In the `fields` property, after the `selected_m3us` field block (ends ~line 426), add:

```python
            {
                "id": "custom_aliases",
                "label": "🔤 Custom Aliases (JSON)",
                "type": "string",
                "default": PluginConfig.DEFAULT_CUSTOM_ALIASES,
                "placeholder": '{"My Channel": ["Provider Stream Name", "Alt Name"]}',
                "help_text": "JSON object mapping a channel name to extra stream-name aliases (a bare string is accepted as a single alias). Streams whose name exactly matches an alias are force-matched to that channel. Leave blank to use built-in aliases only.",
            },
```

- [ ] **Step 3: Verify compile + version sync**

Run:
```bash
python -m py_compile Stream-Mapparr/plugin.py
python scripts/check_version_sync.py
```
Expected: both succeed (no version drift yet — version bump is Task 9).

- [ ] **Step 4: Commit**

```bash
git add Stream-Mapparr/plugin.py
git commit -m "feat: add custom_aliases setting field"
```

---

## Task 9: Version bump, full validation, push

**Files:**
- Modify: `Stream-Mapparr/plugin.json`, `Stream-Mapparr/plugin.py` (via bump script)

- [ ] **Step 1: Bump the plugin version**

Run: `python Stream-Mapparr/bump_version.py`
Expected: `plugin.json` + `plugin.py` version strings updated in sync (calver `1.MAJOR.DDDHHMM`).

- [ ] **Step 2: Run the full validation suite**

Run:
```bash
python -m py_compile Stream-Mapparr/plugin.py
python scripts/check_version_sync.py
python scripts/validate_databases.py
python -m pytest -q
```
Expected: all pass; pytest green (105 prior + new alias/aliases tests).

- [ ] **Step 3: Commit the bump**

```bash
git add Stream-Mapparr/plugin.py Stream-Mapparr/plugin.json
git commit -m "release: bump version for Phase-1 US alias matching"
```

- [ ] **Step 4: Push the branch and open a PR**

```bash
export GH_TOKEN=$(printf "protocol=https\nhost=github.com\n\n" | git credential fill 2>/dev/null | grep '^password=' | cut -d= -f2-)
git push -u origin feature/alias-matching
gh pr create --repo PiratesIRC/Stream-Mapparr --base main --head feature/alias-matching \
  --title "Phase-1 US alias matching + type taxonomy doc fix" \
  --body "Adds an exact-normalized alias layer (abbreviation/rebrand lift, measured ~17% on real US streams), a US alias table, a custom_aliases setting, and a docs-only channel-type taxonomy clarification. Spec: docs/specs/2026-06-13-alias-matching-phase1-design.md. Plan: docs/plans/2026-06-13-alias-matching-phase1.md."
```

- [ ] **Step 5: Confirm CI green**

Run: `gh pr checks <PR#> --repo PiratesIRC/Stream-Mapparr`
Expected: Analyze, CodeQL, test all pass.

---

## Self-Review (completed by plan author)

**Spec coverage:**
- Deliverable 1 (taxonomy docs + PR #30) → Task 0. ✅
- `alias_lookup` (exact-normalized, no helpers) → Task 2. ✅
- `aliases.py` US table + guards → Task 1. ✅
- `_build_alias_map` (+ custom_aliases parse/merge) → Task 3. ✅
- Call-site force-include (the blocker fix) → Tasks 4, 6, 7. ✅
- `custom_aliases` setting → Task 8. ✅
- Guards: bare-token (Task 1 test), country scaffold (Task 3), callsign precedence (alias runs after callsign path — preserved, the fuzzy block is already after callsign return), numeric-sibling safety (Task 2 test). ✅
- Tests: critical blocker lock (Task 6), threshold path (Task 7), lift cases / numeric-sibling / bare-token / custom parse (Tasks 1-4). ✅
- Versioning/deploy → Task 9. ✅

**Placeholder scan:** Alias table content is specified by exact Lineuparr source line ranges (data copy, not a code placeholder); all code steps contain full code. Two steps carry explicit "read & confirm shape before editing" notes for the delicate `plugin.py` edits — these are verification instructions, not placeholders.

**Type consistency:** `alias_lookup(query_name, candidate_names, alias_map, ...)` → list[str]; `_collect_alias_streams(channel_name, working_streams, ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc)` → list[dict]; `_build_alias_map(settings, country)` → dict; `self._alias_map` set at both init sites. Names consistent across Tasks 2-7.
