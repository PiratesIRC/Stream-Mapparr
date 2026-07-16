"""Tests for pure helpers in plugin.py.

plugin.py imports Django + the Dispatcharr ORM at module load, so conftest stubs
those before import. We exercise the genuinely pure helpers — the ones whose past
breakage is recorded in .wolf/buglog.json / cerebrum Do-Not-Repeat.
"""

from datetime import datetime, timedelta, timezone as dt_tz

import pytest


# --------------------------------------------------------------------------- #
# _parse_tags — the canonical quote-aware comma parser
# --------------------------------------------------------------------------- #

def test_parse_tags_basic(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags("a, b ,c") == ["a", "b", "c"]


def test_parse_tags_empty(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags("") == []
    assert Plugin._parse_tags("   ") == []
    assert Plugin._parse_tags(None) == []


def test_parse_tags_quoted_comma_is_literal(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags('"a, b", c') == ["a, b", "c"]


def test_parse_tags_drops_empties(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_tags("a,,b,") == ["a", "b"]


# --------------------------------------------------------------------------- #
# _parse_priority_list — lowercased delegation to _parse_tags
# --------------------------------------------------------------------------- #

def test_parse_priority_list_lowercases(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_priority_list("EAC3, AC3") == ["eac3", "ac3"]


def test_parse_priority_list_empty_inputs(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._parse_priority_list("") == []
    assert Plugin._parse_priority_list(None) == []


# --------------------------------------------------------------------------- #
# _audio_rank — empty list no-op, substring match, unlisted=worst
# --------------------------------------------------------------------------- #

def test_audio_rank_empty_list_is_noop(plugin_module):
    Plugin = plugin_module.Plugin
    assert Plugin._audio_rank("5.1", []) == 0
    assert Plugin._audio_rank(None, []) == 0


def test_audio_rank_returns_index(plugin_module):
    Plugin = plugin_module.Plugin
    pri = ["5.1", "stereo"]
    assert Plugin._audio_rank("5.1", pri) == 0
    assert Plugin._audio_rank("stereo", pri) == 1


def test_audio_rank_substring_match(plugin_module):
    """ffprobe emits both '5.1' and '5.1(side)' for one layout -> substring, not exact."""
    Plugin = plugin_module.Plugin
    assert Plugin._audio_rank("5.1(side)", ["5.1"]) == 0


def test_audio_rank_unlisted_and_missing_sort_last(plugin_module):
    Plugin = plugin_module.Plugin
    pri = ["5.1", "stereo"]
    assert Plugin._audio_rank("mono", pri) == len(pri)
    assert Plugin._audio_rank(None, pri) == len(pri)
    assert Plugin._audio_rank("", pri) == len(pri)


# --------------------------------------------------------------------------- #
# _is_probe_fresh — failed probes (null mbps) are NEVER fresh (bug-008)
# --------------------------------------------------------------------------- #

def _bare_plugin(plugin_module):
    """A Plugin instance without running __init__ (the helper needs no state)."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_probe_fresh_recent_real_measurement(plugin_module):
    p = _bare_plugin(plugin_module)
    now = datetime.now(dt_tz.utc)
    entry = {"throughput_mbps": 12.3, "throughput_measured_at": now.isoformat()}
    assert p._is_probe_fresh(entry, ttl_minutes=30) is True


def test_probe_with_null_mbps_is_never_fresh(plugin_module):
    """bug-008: a fresh timestamp + null mbps must NOT block re-probing."""
    p = _bare_plugin(plugin_module)
    now = datetime.now(dt_tz.utc)
    entry = {"throughput_mbps": None, "throughput_measured_at": now.isoformat()}
    assert p._is_probe_fresh(entry, ttl_minutes=30) is False


def test_probe_expired_is_not_fresh(plugin_module):
    p = _bare_plugin(plugin_module)
    old = datetime.now(dt_tz.utc) - timedelta(minutes=45)
    entry = {"throughput_mbps": 5.0, "throughput_measured_at": old.isoformat()}
    assert p._is_probe_fresh(entry, ttl_minutes=30) is False


def test_probe_missing_or_malformed_entry(plugin_module):
    p = _bare_plugin(plugin_module)
    assert p._is_probe_fresh(None, ttl_minutes=30) is False
    assert p._is_probe_fresh({}, ttl_minutes=30) is False
    assert p._is_probe_fresh(
        {"throughput_mbps": 5.0, "throughput_measured_at": "not-a-date"}, 30) is False


# --------------------------------------------------------------------------- #
# _deduplicate_streams — keyed on (name, m3u_account) for multi-source failover
#   (issue #28 / PR #29). Dedup always runs AFTER _sort_streams_by_quality, so
#   "keep first occurrence" keeps the highest-quality stream per key.
# --------------------------------------------------------------------------- #

def test_dedup_collapses_same_name_same_account(plugin_module):
    """True duplicates within one provider collapse to the first (best) one."""
    p = _bare_plugin(plugin_module)
    streams = [
        {"name": "ESPN HD", "m3u_account": 5, "id": 1},  # best (sorted first)
        {"name": "ESPN HD", "m3u_account": 5, "id": 2},  # dup within same source
    ]
    out = p._deduplicate_streams(streams)
    assert [s["id"] for s in out] == [1]


def test_dedup_drops_nameless_streams(plugin_module):
    p = _bare_plugin(plugin_module)
    streams = [{"name": "", "m3u_account": 5}, {"name": "ESPN HD", "m3u_account": 5}]
    out = p._deduplicate_streams(streams)
    assert [s["name"] for s in out] == ["ESPN HD"]


def test_dedup_keeps_same_name_from_different_accounts(plugin_module):
    """Multi-source failover (issue #28): identical names from DIFFERENT
    providers both survive; only same-source duplicates collapse."""
    p = _bare_plugin(plugin_module)
    streams = [
        {"name": "ESPN HD", "m3u_account": 5, "id": 1},
        {"name": "ESPN HD", "m3u_account": 9, "id": 2},  # different provider
    ]
    out = p._deduplicate_streams(streams)
    assert [s["id"] for s in out] == [1, 2]


# --------------------------------------------------------------------------- #
# _labeled_stream_names — tag CSV stream names with their M3U source so
#   multi-source copies (same name, different provider) are distinguishable.
# --------------------------------------------------------------------------- #

def test_labeled_stream_names_tags_source(plugin_module):
    Plugin = plugin_module.Plugin
    streams = [
        {"name": "GO: CNN", "m3u_account": 5},
        {"name": "GO: CNN", "m3u_account": 9},   # same name, different provider
        {"name": "US: CNN HD", "m3u_account": 5},
    ]
    name_map = {5: "streamq-bk15", 9: "streamq-bk26"}
    out = Plugin._labeled_stream_names(streams, name_map)
    assert out == [
        "GO: CNN [streamq-bk15]",
        "GO: CNN [streamq-bk26]",
        "US: CNN HD [streamq-bk15]",
    ]


def test_labeled_stream_names_unknown_or_missing_account_unlabeled(plugin_module):
    Plugin = plugin_module.Plugin
    streams = [{"name": "Foo", "m3u_account": 99}, {"name": "Bar"}]
    out = Plugin._labeled_stream_names(streams, {5: "x"})
    assert out == ["Foo", "Bar"]  # unknown / missing account -> no tag


# --------------------------------------------------------------------------- #
# Scheduler bootstrap — __init__ must arm the background scheduler (bug-065)
# --------------------------------------------------------------------------- #

def test_init_bootstraps_scheduler_via_load_settings(plugin_module, monkeypatch):
    """bug-065: Dispatcharr re-instantiates the Plugin (calls __init__) but does NOT
    reliably call on_load in this environment, so the scheduler never armed and
    scheduled runs silently stopped. __init__ must bootstrap the scheduler the same
    way the working sibling (event_channel_managarr) does — via _load_settings()."""
    Plugin = plugin_module.Plugin
    calls = []
    monkeypatch.setattr(Plugin, "_load_settings", lambda self: calls.append(True))
    Plugin()
    assert calls == [True], "__init__ must call _load_settings() to arm the scheduler"


def test_rearm_with_unchanged_schedule_keeps_same_thread(plugin_module):
    """bug-127: __init__ arms the scheduler (bug-065) and Dispatcharr re-instantiates
    the Plugin on nearly every request, so _start_background_scheduler runs constantly.
    The old code unconditionally stopped (lock + set-event + join) and relaunched the
    scheduler thread on EVERY call; with a schedule configured there was always a live
    thread to join, so each channels API call (e.g. the 'retrieve channels by UUIDs' a
    channel zap fires) paid that cost and serialized behind the global lock until nginx
    504'd. Re-arming with an UNCHANGED schedule must be a no-op: same thread, no churn."""
    Plugin = plugin_module.Plugin
    p = Plugin()
    settings = {"scheduled_times": "0330"}
    try:
        p._start_background_scheduler(settings)
        t1 = plugin_module._scheduler_state().bg_thread
        assert t1 is not None and t1.is_alive(), "first arm must start a scheduler thread"
        p._start_background_scheduler(settings)
        p._start_background_scheduler(settings)
        t2 = plugin_module._scheduler_state().bg_thread
        assert t2 is t1, "re-arm with unchanged schedule must not replace the thread"
        assert t1.is_alive()
    finally:
        p._stop_background_scheduler()


def test_rearm_with_changed_schedule_restarts_thread(plugin_module):
    """The idempotent guard must still (re)start when the schedule actually changes,
    so an in-UI schedule edit takes effect."""
    Plugin = plugin_module.Plugin
    p = Plugin()
    try:
        p._start_background_scheduler({"scheduled_times": "0330"})
        t1 = plugin_module._scheduler_state().bg_thread
        p._start_background_scheduler({"scheduled_times": "0445"})
        t2 = plugin_module._scheduler_state().bg_thread
        assert t2 is not t1, "a changed schedule must restart the scheduler thread"
        assert t2.is_alive()
    finally:
        p._stop_background_scheduler()


# --------------------------------------------------------------------------- #
# Zone-aware routing — Starz East/West (bug-068)
# --------------------------------------------------------------------------- #
def test_zone_affinity_rank_west_channel_prefers_west(plugin_module):
    f = plugin_module._zone_affinity_rank
    assert f('WEST', 'WEST') < f('WEST', 'DEFAULT') < f('WEST', 'EAST')


def test_zone_affinity_rank_default_channel_is_east_like(plugin_module):
    f = plugin_module._zone_affinity_rank
    assert f('DEFAULT', 'EAST') == 0
    assert f('DEFAULT', 'DEFAULT') == 0
    assert f('DEFAULT', 'WEST') == 2


def test_zone_routed_map_marks_siblings_and_lone_default(plugin_module, matcher):
    """A channel is zone-ordered when it has a different-zone sibling (bug-068) OR when
    it is itself unmarked/DEFAULT (bug-126 follow-up: a lone unmarked channel like
    'Cinemax' must prefer its East/national feed over any West alternates it picked up).
    A lone MARKED channel is still left alone, which keeps the brand-word false positives
    ('Key West' -> WEST) harmless."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = matcher()
    channels = [
        {'id': 1, 'name': 'Starz Encore'},        # DEFAULT, has a West sibling
        {'id': 2, 'name': 'STARZ Encore (W)'},     # WEST (has an East/default sibling)
        {'id': 3, 'name': 'Cinemax'},              # lone DEFAULT -> now routed (prefer East)
        {'id': 4, 'name': 'Key West'},             # lone WEST brand-word -> NOT routed
    ]
    routed = p._zone_routed_map(channels, None, True, True, True, True)
    assert routed == {1: 'DEFAULT', 2: 'WEST', 3: 'DEFAULT'}   # Key West (4) NOT routed


def test_order_streams_for_zone_west_channel_promotes_west(plugin_module, matcher):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = matcher()
    streams = [  # input already quality-sorted; East is "best quality" here
        {'id': 10, 'name': 'STARZ ENCORE EAST HD'},
        {'id': 11, 'name': 'STARZ ENCORE HD'},
        {'id': 12, 'name': 'STARZ ENCORE WEST HD'},
    ]
    ordered = p._order_streams_for_zone(streams, 'WEST')
    assert ordered[0]['id'] == 12    # WEST promoted to primary
    assert ordered[-1]['id'] == 10   # EAST demoted to last (fallback)


def test_channels_to_update_includes_each_zone_sibling(plugin_module):
    """bug-068 bypass: at limit=1 a same-group West sibling is still assigned, but a
    duplicate same-zone channel is NOT (the limit still caps true duplicates)."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    sorted_channels = [{'id': 1}, {'id': 2}, {'id': 3}]   # 1,2 = DEFAULT; 3 = WEST
    zone_routed = {1: 'DEFAULT', 2: 'DEFAULT', 3: 'WEST'}
    out = p._channels_to_update_for_group(sorted_channels, 1, zone_routed)
    assert [c['id'] for c in out] == [1, 3]   # top DEFAULT + the West sibling; dup DEFAULT excluded


def test_channels_to_update_no_zone_routing_respects_limit(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    out = p._channels_to_update_for_group([{'id': 1}, {'id': 2}], 1, {})
    assert [c['id'] for c in out] == [1]   # common case unchanged


def test_streams_for_channel_reorders_only_routed(plugin_module, matcher):
    """bug-068 shared seam: a routed channel gets zone-ordered streams; a non-routed
    channel gets the original list untouched (same object). Shared by Match & Assign,
    Sort, and Preview so all three agree on per-channel order."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = matcher()
    streams = [{'id': 1, 'name': 'X EAST'}, {'id': 2, 'name': 'X WEST'}]
    routed = p._streams_for_channel(streams, 5, {5: 'WEST'})
    assert [s['id'] for s in routed] == [2, 1]                  # West first
    assert p._streams_for_channel(streams, 5, {}) is streams    # non-routed unchanged


# --------------------------------------------------------------------------- #
# Cross-worker scheduled-slot claim — bug-069 (multi-worker duplicate runs)
# --------------------------------------------------------------------------- #
@pytest.fixture
def tmp_sched(plugin_module, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "SCHEDULER_LAST_RUN_FILE",
                        str(tmp_path / "sched_last_run.json"))
    monkeypatch.setattr(plugin_module.PluginConfig, "SCHEDULER_LOCK_FILE",
                        str(tmp_path / "sched.lock"))
    return tmp_path


def test_claim_scheduled_slot_dedups_same_slot(plugin_module, tmp_sched):
    """First worker wins the slot; a second worker (same slot+date) is told to skip."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    import logging
    log = logging.getLogger("t")
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is True
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is False
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is False


def test_claim_scheduled_slot_new_day_reclaims(plugin_module, tmp_sched):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    import logging
    log = logging.getLogger("t")
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is True
    assert p._claim_scheduled_slot("05:00", "2026-06-25", log) is True   # next day -> fresh
    assert p._claim_scheduled_slot("05:00", "2026-06-25", log) is False


def test_claim_scheduled_slot_independent_times(plugin_module, tmp_sched):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    import logging
    log = logging.getLogger("t")
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is True
    assert p._claim_scheduled_slot("16:00", "2026-06-24", log) is True   # different time slot


def test_claim_scheduled_slot_survives_flock_failure(plugin_module, tmp_sched, monkeypatch):
    """QA bug-069: if flock raises (e.g. ENOLCK on some filesystems) after the lock
    file opened, the except branch must still close the fd (no leak) and the claim
    must proceed degraded (no cross-worker guard) without crashing — and still
    function (stamp + dedup)."""
    import logging, types
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    log = logging.getLogger("t")

    def boom(*a, **k):
        raise OSError("no lock on this filesystem")

    monkeypatch.setattr(plugin_module, "fcntl",
                        types.SimpleNamespace(LOCK_EX=2, LOCK_UN=8, flock=boom))
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is True    # proceeds degraded
    assert p._claim_scheduled_slot("05:00", "2026-06-24", log) is False   # still dedups via the file
