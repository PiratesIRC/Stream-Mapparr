"""The quality sort must use the settings the running action was given.

_sort_streams_by_quality reads the placeholder rule and the throughput flags
from instance state. When that state is not primed it primes it from
saved_settings, a copy that only Update Schedule rewrites, so it can disagree
with the form. Every action that sorts used to leave the state unprimed, which
let saved_settings decide: Sort resolved Demote Placeholder Streams from its
live settings and then had that value replaced by the saved copy inside the
sort, and Preview and Match and Assign never resolved it at all.

Each action now primes the state from its own live settings before sorting.
"""


# A slate claims 1080p while carrying almost no video; the real stream is 720p.
# With demotion OFF the slate wins on resolution; with it ON it is moved last.
SLATE = {"id": 1, "name": "slate",
         "stats": {"width": 1920, "height": 1080, "source_fps": 50, "video_bitrate": 190}}
REAL = {"id": 2, "name": "real",
        "stats": {"width": 1280, "height": 720, "source_fps": 50, "video_bitrate": 3000}}

LIVE = {"profile_name": "Default", "demote_content_starved": False,
        "enable_throughput_sorting": False}
STALE = {"demote_content_starved": True, "enable_throughput_sorting": True}


class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def _instance(plugin_module, monkeypatch):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.saved_settings = dict(STALE)
    inst._throughput_state_primed = False  # what run() leaves behind
    monkeypatch.setattr(inst, "_load_throughput_cache",
                        lambda: setattr(inst, "_throughput_cache", {}), raising=False)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None, raising=False)
    return inst


def _sorted(inst):
    return [s["name"] for s in inst._sort_streams_by_quality([REAL, SLATE])]


def test_sort_action_keeps_the_live_placeholder_setting_through_the_sort(
        plugin_module, monkeypatch):
    inst = _instance(plugin_module, monkeypatch)
    inst.sort_streams_action(dict(LIVE), _Logger())
    assert _sorted(inst) == ["slate", "real"]
    assert inst._throughput_sorting_enabled is False


def test_load_process_channels_primes_from_live_settings(plugin_module, monkeypatch):
    """Preview and Match and Assign both start with this action."""
    inst = _instance(plugin_module, monkeypatch)
    monkeypatch.setattr(inst, "_ensure_matcher_and_aliases", lambda s: None, raising=False)
    monkeypatch.setattr(inst, "_validate_plugin_settings", lambda s, l: (False, []), raising=False)
    inst.load_process_channels_action(dict(LIVE), _Logger())
    assert _sorted(inst) == ["slate", "real"]
    assert inst._throughput_sorting_enabled is False


def test_the_saved_copy_still_applies_when_no_action_primed(plugin_module, monkeypatch):
    """Control: with nothing primed, the fallback reads saved_settings, so the
    two tests above are measuring the priming and not something else."""
    inst = _instance(plugin_module, monkeypatch)
    assert _sorted(inst) == ["real", "slate"]
