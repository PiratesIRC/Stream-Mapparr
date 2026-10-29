"""Measured bitrate breaks a tie between streams that match on everything else.

Reported 2026-10-29: a provider publishes "HEVC HB" and "HEVC LB" feeds of the
same channel. IPTV Checker records identical resolution, frame rate, codec and
audio for both, and a video bitrate twice as high on HB. The quality sort never
read the bitrate, so the two tied and kept their existing channel order, which
put LB first on every channel that carried both.

The bitrate is rounded down to a step (Bitrate Sort Step, default 500 kbps)
before it is compared. A variable bitrate stream measures slightly differently
on every IPTV Checker run, and comparing raw values would let two near-identical
streams swap places from one day's Sort to the next.
"""
import pytest


def _stream(sid, name, height=1080, width=1920, fps=50.0,
            video_bitrate=None, output_bitrate=None):
    stats = {"height": height, "width": width, "source_fps": fps}
    if video_bitrate is not None:
        stats["video_bitrate"] = video_bitrate
    if output_bitrate is not None:
        stats["ffmpeg_output_bitrate"] = output_bitrate
    return {"id": sid, "name": name, "stats": stats}


def _plugin(plugin_module, step=500):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._prioritize_quality = False
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._bitrate_sort_step_kbps = step
    p._throughput_state_primed = True
    p._throughput_sorting_enabled = False
    p.saved_settings = {}
    return p


def _names(p, streams):
    return [s["name"] for s in p._sort_streams_by_quality(streams)]


HB = _stream(1, "HEVC HB", video_bitrate=4000)
LB = _stream(2, "HEVC LB", video_bitrate=2000)


# --------------------------------------------------------------------------- #
# The reported case
# --------------------------------------------------------------------------- #

def test_the_higher_bitrate_stream_ranks_first_whatever_the_input_order(plugin_module):
    p = _plugin(plugin_module)
    assert _names(p, [LB, HB]) == ["HEVC HB", "HEVC LB"]
    assert _names(p, [HB, LB]) == ["HEVC HB", "HEVC LB"]


def test_bitrate_never_outranks_resolution(plugin_module):
    """A tie-breaker only: a 720p stream at a higher bitrate stays behind 1080p."""
    p = _plugin(plugin_module)
    sd_high = _stream(3, "720 high", height=720, width=1280, video_bitrate=9000)
    assert _names(p, [sd_high, LB]) == ["HEVC LB", "720 high"]


def test_bitrate_never_outranks_frame_rate(plugin_module):
    p = _plugin(plugin_module)
    slow_high = _stream(3, "25fps high", fps=25.0, video_bitrate=9000)
    assert _names(p, [slow_high, LB]) == ["HEVC LB", "25fps high"]


def test_bitrate_never_outranks_source_priority(plugin_module):
    p = _plugin(plugin_module)
    first_source = dict(LB, _m3u_priority=0)
    second_source = dict(HB, _m3u_priority=1)
    assert _names(p, [second_source, first_source]) == ["HEVC LB", "HEVC HB"]


# --------------------------------------------------------------------------- #
# The step: small differences keep the existing order
# --------------------------------------------------------------------------- #

def test_bitrates_inside_one_step_keep_the_existing_order(plugin_module):
    p = _plugin(plugin_module)
    a = _stream(1, "a", video_bitrate=2100)
    b = _stream(2, "b", video_bitrate=2400)
    assert _names(p, [a, b]) == ["a", "b"]
    assert _names(p, [b, a]) == ["b", "a"]


def test_a_larger_step_absorbs_a_larger_difference(plugin_module):
    p = _plugin(plugin_module, step=5000)
    assert _names(p, [LB, HB]) == ["HEVC LB", "HEVC HB"]


def test_a_zero_step_turns_the_tie_breaker_off(plugin_module):
    p = _plugin(plugin_module, step=0)
    assert _names(p, [LB, HB]) == ["HEVC LB", "HEVC HB"]


# --------------------------------------------------------------------------- #
# Missing and disagreeing evidence
# --------------------------------------------------------------------------- #

def test_a_stream_with_no_bitrate_ranks_behind_one_with_a_bitrate(plugin_module):
    p = _plugin(plugin_module)
    unmeasured = _stream(3, "unmeasured")
    assert _names(p, [unmeasured, LB]) == ["HEVC LB", "unmeasured"]


def test_a_zero_bitrate_counts_as_unmeasured(plugin_module):
    p = _plugin(plugin_module)
    zero = _stream(3, "zero", video_bitrate=0, output_bitrate=0)
    assert _names(p, [zero, LB]) == ["HEVC LB", "zero"]


def test_the_higher_of_the_two_bitrate_fields_is_used(plugin_module):
    p = _plugin(plugin_module)
    output_only_high = _stream(3, "output high", video_bitrate=1000, output_bitrate=4000)
    assert _names(p, [LB, output_only_high]) == ["output high", "HEVC LB"]


def test_streams_without_dimensions_still_sort(plugin_module):
    """The no-dimension branch builds its own key; mixing both must not raise."""
    p = _plugin(plugin_module)
    bare = {"id": 9, "name": "bare", "stats": {}}
    assert _names(p, [bare, LB, HB]) == ["HEVC HB", "HEVC LB", "bare"]


def test_throughput_sorting_still_composes(plugin_module):
    p = _plugin(plugin_module)
    p._throughput_sorting_enabled = True
    p._throughput_cache = {}
    p._bitrate_safety_margin = 1.1
    assert _names(p, [LB, HB]) == ["HEVC HB", "HEVC LB"]


# --------------------------------------------------------------------------- #
# Settings resolution, from the LIVE settings dict (bug-139)
# --------------------------------------------------------------------------- #

def test_the_step_resolves_from_the_live_settings_dict(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._resolve_bitrate_sort_step({"bitrate_sort_step_kbps": 1000}) == 1000


def test_zero_is_kept_because_it_means_off(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._resolve_bitrate_sort_step({"bitrate_sort_step_kbps": 0}) == 0


@pytest.mark.parametrize("bad", ["", None, "abc", -5])
def test_an_unusable_step_falls_back_to_the_default(plugin_module, bad):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    default = plugin_module.PluginConfig.DEFAULT_BITRATE_SORT_STEP_KBPS
    assert default == 500
    assert p._resolve_bitrate_sort_step({"bitrate_sort_step_kbps": bad}) == default
    assert p._resolve_bitrate_sort_step({}) == default


def test_the_setting_is_declared_in_the_stream_selection_section(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    ids = [f["id"] for f in p.fields]
    assert "bitrate_sort_step_kbps" in ids
    start = ids.index("_section_stream_selection")
    end = min(i for i, fid in enumerate(ids) if fid.startswith("_section_") and i > start)
    assert start < ids.index("bitrate_sort_step_kbps") < end


class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def test_sort_streams_action_resolves_the_step_on_its_own_path(plugin_module, monkeypatch):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)
    log = _Logger()
    inst.sort_streams_action(
        {"profile_name": "Default", "bitrate_sort_step_kbps": 750}, log)
    assert inst._bitrate_sort_step_kbps == 750
    assert any("Bitrate sort step" in m for m in log.messages)


def test_load_process_channels_resolves_the_step_from_live_settings(plugin_module, monkeypatch):
    """Preview and Match and Assign both go through this action."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_ensure_matcher_and_aliases", lambda s: None, raising=False)
    monkeypatch.setattr(inst, "_validate_plugin_settings", lambda s, l: (False, []), raising=False)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)
    inst.load_process_channels_action(
        {"profile_name": "Default", "bitrate_sort_step_kbps": 750}, _Logger())
    assert inst._bitrate_sort_step_kbps == 750


def test_priming_from_saved_settings_does_not_overwrite_the_live_step(plugin_module, monkeypatch):
    """The sort primes throughput state from saved_settings, a copy only Update
    Schedule rewrites. It must not replace the step resolved from live settings."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_load_throughput_cache", lambda: None, raising=False)
    inst._bitrate_sort_step_kbps = 0
    inst._prime_throughput_state({"bitrate_sort_step_kbps": 500})
    assert inst._bitrate_sort_step_kbps == 0


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), "inf", "nan"])
def test_a_non_finite_bitrate_counts_as_unmeasured_and_does_not_crash(plugin_module, bad):
    p = _plugin(plugin_module)
    odd = _stream(3, "odd", video_bitrate=bad)
    assert _names(p, [odd, LB]) == ["HEVC LB", "odd"]


@pytest.mark.parametrize("bad", [float("inf"), "inf", float("nan")])
def test_a_non_finite_step_falls_back_to_the_default(plugin_module, bad):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    default = plugin_module.PluginConfig.DEFAULT_BITRATE_SORT_STEP_KBPS
    assert p._resolve_bitrate_sort_step({"bitrate_sort_step_kbps": bad}) == default
