"""Tests for `prioritize_quality` in the Sort Streams action (bug-139).

`prioritize_quality` was resolved from settings in exactly ONE place —
`load_process_channels_action` — which stashed it on the instance as
`self._prioritize_quality`. The comparator in `_sort_streams_by_quality` reads it
back with `getattr(self, '_prioritize_quality', False)`.

`sort_streams_action` never calls `load_process_channels_action`, so that attribute
was never set on the Sort path and the `getattr` default silently won: Sort ordered
by `(m3u_priority, tier, ...)` — source first, quality second — no matter what the
user had ticked. On a box in Dispatcharr's reload-token ping-pong (bug-136) the
instance is rebuilt per request, so a preceding Match & Assign could not leak the
attribute in either.

Reporter's symptom (UK: SKY NEWS, 9 streams): Strong TV x3, Dream x3, Promax x3 —
quality ordered only WITHIN each source.
"""
import pytest


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def _stream(sid, name, m3u_priority, width, height, fps):
    return {
        "id": sid,
        "name": name,
        "_m3u_priority": m3u_priority,
        "stats": {"width": width, "height": height, "source_fps": fps},
    }


# The reporter's channel: a 4K and an HD feed on the LOWEST-priority source, and
# an SD feed on the HIGHEST-priority source.
def _reporter_streams():
    return [
        _stream(1, "UK: SKY NEWS SD (Strong TV)", 0, 720, 576, 25),
        _stream(2, "UK: SKY NEWS HEVC 4K (Promax)", 2, 3840, 2160, 50),
        _stream(3, "UK: SKY NEWS HD (Promax)", 2, 1920, 1080, 50),
    ]


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "value,expected",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("1", True),
        ("false", False),
        ("0", False),
        ("", False),
    ],
)
def test_resolve_prioritize_quality_coerces_like_the_other_resolvers(
    plugin_module, value, expected
):
    inst = _bare(plugin_module)
    assert inst._resolve_prioritize_quality({"prioritize_quality": value}) is expected


def test_resolve_prioritize_quality_falls_back_to_the_documented_default(plugin_module):
    inst = _bare(plugin_module)
    expected = bool(plugin_module.PluginConfig.DEFAULT_PRIORITIZE_QUALITY)
    assert inst._resolve_prioritize_quality({}) is expected


# --------------------------------------------------------------------------- #
# The comparator honours the resolved flag
# --------------------------------------------------------------------------- #

def test_quality_first_beats_source_when_flag_is_set(plugin_module):
    inst = _bare(plugin_module)
    inst.saved_settings = {}
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst._prioritize_quality = True

    order = [s["id"] for s in inst._sort_streams_by_quality(_reporter_streams())]
    # 4K then HD (both tier 0, ranked by pixels) then the SD feed, ignoring source.
    assert order == [2, 3, 1]


def test_source_first_is_preserved_when_flag_is_unset(plugin_module):
    inst = _bare(plugin_module)
    inst.saved_settings = {}
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst._prioritize_quality = False

    order = [s["id"] for s in inst._sort_streams_by_quality(_reporter_streams())]
    # Highest-priority source leads even though it is the worst picture.
    assert order == [1, 2, 3]


# --------------------------------------------------------------------------- #
# bug-139: the Sort action must resolve the flag ITSELF
# --------------------------------------------------------------------------- #

def test_sort_streams_action_resolves_prioritize_quality_from_live_settings(
    plugin_module, monkeypatch
):
    """Sort must not depend on a prior load_process_channels_action having run.

    Driven only as far as the profile lookup — enough to prove the resolution
    happens on the Sort path itself, with no ORM required.
    """
    inst = _bare(plugin_module)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)

    result = inst.sort_streams_action(
        {"profile_name": "Default", "prioritize_quality": True}, _Logger()
    )

    # Bailed out at the profile lookup, but the flag was already resolved.
    assert result["status"] == "error"
    assert inst._prioritize_quality is True


def test_sort_streams_action_does_not_inherit_a_stale_flag(plugin_module, monkeypatch):
    """An unticked setting must clear a True left over from an earlier action."""
    inst = _bare(plugin_module)
    inst._prioritize_quality = True
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)

    inst.sort_streams_action(
        {"profile_name": "Default", "prioritize_quality": False}, _Logger()
    )

    assert inst._prioritize_quality is False
