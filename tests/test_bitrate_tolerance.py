"""Bitrate Sort Tolerance: a small bitrate change does not reorder two streams.

Measured 2026-11-03 on the live box: the after-scan Sort changed 288 channels
on 10-30 and 240 on 11-02, against 90 to 118 before the bitrate tie-breaker.
Between two IPTV Checker scans, 357 streams moved from one 500 kbps step to
another and then back again, and more than 500 streams per scan changed step
while moving by less than 500 kbps. A variable bitrate stream measures
differently on every scan, so a fixed step boundary reorders near-equal
streams over and over.

Two streams now change places only when their bitrates fall in different steps
AND the later stream's bitrate is more than the tolerance (default 20 percent)
above the earlier one's. Otherwise they keep the channel's current order. A
simulation on the 10-30 and 11-02 scans cut the channels whose bitrate order
changed from 319 and 348 to 222 and 221.
"""
import pytest


def _stream(sid, name, height=1080, width=1920, fps=50.0, video_bitrate=None):
    stats = {"height": height, "width": width, "source_fps": fps}
    if video_bitrate is not None:
        stats["video_bitrate"] = video_bitrate
    return {"id": sid, "name": name, "stats": stats}


def _plugin(plugin_module, step=500, tolerance=20):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._prioritize_quality = False
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._bitrate_sort_step_kbps = step
    p._bitrate_sort_tolerance_pct = tolerance
    p._throughput_state_primed = True
    p._throughput_sorting_enabled = False
    p.saved_settings = {}
    return p


def _names(p, streams):
    return [s["name"] for s in p._sort_streams_by_quality(streams)]


# --------------------------------------------------------------------------- #
# The measured fault: a step boundary crossed by a small change
# --------------------------------------------------------------------------- #

def test_a_small_change_across_a_step_boundary_keeps_the_current_order(plugin_module):
    """2400 and 2600 kbps are in different 500 kbps steps but only 8 percent apart."""
    p = _plugin(plugin_module)
    a = _stream(1, "a", video_bitrate=2400)
    b = _stream(2, "b", video_bitrate=2600)
    assert _names(p, [a, b]) == ["a", "b"]
    assert _names(p, [b, a]) == ["b", "a"]


def test_a_large_difference_still_reorders(plugin_module):
    p = _plugin(plugin_module)
    lb = _stream(1, "LB", video_bitrate=2000)
    hb = _stream(2, "HB", video_bitrate=4000)
    assert _names(p, [lb, hb]) == ["HB", "LB"]
    assert _names(p, [hb, lb]) == ["HB", "LB"]


def test_just_over_the_tolerance_reorders_and_just_under_does_not(plugin_module):
    p = _plugin(plugin_module)
    # 2400 is in the 2000 step; both others are in the 2500 step.
    base = _stream(1, "base", video_bitrate=2400)
    under = _stream(2, "under", video_bitrate=2870)   # 19.6 percent higher
    over = _stream(3, "over", video_bitrate=2890)     # 20.4 percent higher
    assert _names(p, [base, under]) == ["base", "under"]
    assert _names(p, [base, over]) == ["over", "base"]


def test_a_difference_inside_one_step_never_reorders_even_above_the_tolerance(plugin_module):
    """The step still applies: 600 and 900 kbps share the 500 to 999 step."""
    p = _plugin(plugin_module)
    low = _stream(1, "low", video_bitrate=600)
    high = _stream(2, "high", video_bitrate=900)
    assert _names(p, [low, high]) == ["low", "high"]


def test_zero_tolerance_gives_the_previous_step_only_behaviour(plugin_module):
    p = _plugin(plugin_module, tolerance=0)
    a = _stream(1, "a", video_bitrate=2400)
    b = _stream(2, "b", video_bitrate=2600)
    assert _names(p, [a, b]) == ["b", "a"]


def test_zero_step_still_turns_the_tie_breaker_off(plugin_module):
    p = _plugin(plugin_module, step=0)
    lb = _stream(1, "LB", video_bitrate=2000)
    hb = _stream(2, "HB", video_bitrate=4000)
    assert _names(p, [lb, hb]) == ["LB", "HB"]


def test_three_streams_settle_with_no_adjacent_pair_out_of_tolerance(plugin_module):
    p = _plugin(plugin_module)
    s = [_stream(1, "1000", video_bitrate=1000),
         _stream(2, "1300", video_bitrate=1300),
         _stream(3, "1700", video_bitrate=1700)]
    out = p._sort_streams_by_quality(s)
    rates = [x["stats"]["video_bitrate"] for x in out]
    assert rates[0] == 1700
    for earlier, later in zip(rates, rates[1:]):
        assert not (later // 500 > earlier // 500 and later > earlier * 1.2)


def test_the_result_does_not_depend_on_repeating_the_sort(plugin_module):
    """Sorting an already sorted list changes nothing, so a daily Sort is stable."""
    p = _plugin(plugin_module)
    s = [_stream(i, str(r), video_bitrate=r)
         for i, r in enumerate([900, 2600, 2400, 5000, 1100, 3000])]
    once = p._sort_streams_by_quality(s)
    twice = p._sort_streams_by_quality(once)
    assert [x["id"] for x in once] == [x["id"] for x in twice]


# --------------------------------------------------------------------------- #
# Rules the tolerance must not change
# --------------------------------------------------------------------------- #

def test_an_unmeasured_stream_still_ranks_behind_a_measured_one(plugin_module):
    p = _plugin(plugin_module)
    unmeasured = _stream(1, "unmeasured")
    measured = _stream(2, "measured", video_bitrate=600)
    assert _names(p, [unmeasured, measured]) == ["measured", "unmeasured"]


def test_resolution_still_outranks_bitrate(plugin_module):
    p = _plugin(plugin_module)
    sd_high = _stream(1, "720 high", height=720, width=1280, video_bitrate=9000)
    hd_low = _stream(2, "1080 low", video_bitrate=2000)
    assert _names(p, [sd_high, hd_low]) == ["1080 low", "720 high"]


def test_source_priority_still_outranks_bitrate(plugin_module):
    p = _plugin(plugin_module)
    first = dict(_stream(1, "first source", video_bitrate=2000), _m3u_priority=0)
    second = dict(_stream(2, "second source", video_bitrate=4000), _m3u_priority=1)
    assert _names(p, [second, first]) == ["first source", "second source"]


def test_bitrate_still_ranks_ahead_of_measured_throughput(plugin_module):
    """With throughput sorting on, bitrate is compared before the throughput speed."""
    p = _plugin(plugin_module)
    p._throughput_sorting_enabled = True
    p._bitrate_safety_margin = 1.1
    p._throughput_cache = {"1": {"throughput_mbps": 50.0}, "2": {"throughput_mbps": 5.0}}
    lb_fast = _stream(1, "LB fast", video_bitrate=2000)
    hb_slow = _stream(2, "HB slow", video_bitrate=4000)
    monkey = lambda stream, cache, margin: 0  # noqa: E731 - same throughput tier for both
    p._classify_stream_throughput = monkey
    assert _names(p, [lb_fast, hb_slow]) == ["HB slow", "LB fast"]


def test_throughput_speed_decides_between_near_equal_bitrates(plugin_module):
    p = _plugin(plugin_module)
    p._throughput_sorting_enabled = True
    p._bitrate_safety_margin = 1.1
    p._throughput_cache = {"1": {"throughput_mbps": 5.0}, "2": {"throughput_mbps": 50.0}}
    p._classify_stream_throughput = lambda stream, cache, margin: 0
    # The slow stream has the slightly higher bitrate, in a higher step, but
    # within the tolerance, so the faster stream wins on throughput.
    slow = _stream(1, "slow", video_bitrate=2600)
    fast = _stream(2, "fast", video_bitrate=2400)
    assert _names(p, [slow, fast]) == ["fast", "slow"]


# --------------------------------------------------------------------------- #
# Settings resolution, from the LIVE settings dict (bug-139)
# --------------------------------------------------------------------------- #

def test_the_tolerance_resolves_from_the_live_settings_dict(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._resolve_bitrate_sort_tolerance({"bitrate_sort_tolerance_pct": 35}) == 35


def test_zero_tolerance_is_kept(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._resolve_bitrate_sort_tolerance({"bitrate_sort_tolerance_pct": 0}) == 0


@pytest.mark.parametrize("bad", ["", None, "abc", -5, float("inf"), float("nan"), "nan"])
def test_an_unusable_tolerance_falls_back_to_the_default(plugin_module, bad):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    default = plugin_module.PluginConfig.DEFAULT_BITRATE_SORT_TOLERANCE_PCT
    assert default == 20
    assert p._resolve_bitrate_sort_tolerance({"bitrate_sort_tolerance_pct": bad}) == default
    assert p._resolve_bitrate_sort_tolerance({}) == default


def test_the_tolerance_is_declared_in_the_stream_selection_section(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    ids = [f["id"] for f in p.fields]
    assert "bitrate_sort_tolerance_pct" in ids
    start = ids.index("_section_stream_selection")
    end = min(i for i, fid in enumerate(ids) if fid.startswith("_section_") and i > start)
    assert start < ids.index("bitrate_sort_tolerance_pct") < end


class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def test_sort_streams_action_resolves_the_tolerance_on_its_own_path(plugin_module, monkeypatch):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)
    log = _Logger()
    inst.sort_streams_action(
        {"profile_name": "Default", "bitrate_sort_tolerance_pct": 35}, log)
    assert inst._bitrate_sort_tolerance_pct == 35
    assert any("Bitrate sort tolerance" in m for m in log.messages)


def test_load_process_channels_resolves_the_tolerance_from_live_settings(plugin_module, monkeypatch):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_ensure_matcher_and_aliases", lambda s: None, raising=False)
    monkeypatch.setattr(inst, "_validate_plugin_settings", lambda s, l: (False, []), raising=False)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)
    inst.load_process_channels_action(
        {"profile_name": "Default", "bitrate_sort_tolerance_pct": 35}, _Logger())
    assert inst._bitrate_sort_tolerance_pct == 35
