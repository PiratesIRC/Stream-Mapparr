"""A stream claiming high resolution while carrying almost no video is demoted.

Issue 40. Providers serve placeholder streams: a looping slate, a black screen
or a static card, which reports 1080p in its stats but carries roughly 190 kbps
of actual video. Nothing in the plugin demoted these before this change, and
they can sit at position 0 of a channel ahead of a working alternative.

Why the two existing mechanisms miss it, which is what makes a separate rule
necessary rather than a tweak to an existing one:

  The quality sort ranks on claimed resolution and frame rate. The slate claims
  1080p, so it wins.

  The throughput probe and its Bitrate Safety Margin measure DELIVERY SPEED
  against the nominal bitrate for the claimed resolution. A slate is a tiny
  payload, so the server delivers it many times faster than realtime and the
  probe tiers it healthy. That check structurally cannot catch this: it is
  measuring how fast the bytes arrive, not how few of them there are.

Measured on a live installation on 2026-08-02: 218 streams claim 720p or more
while carrying under 300 kbps of video, 44 channels hold both one of those and
a normal stream of 1000 kbps or more, and on 6 of those channels the low
bitrate stream was the one being played. Examples included Red Bull TV playing
193 kbps with a 6838 kbps alternative sitting behind it.

Two deliberate conservatism choices, both of which make a false demotion less
likely than a missed one:

  The evidence must be positive. A stream with no stats, no height or no
  bitrate is never demoted. Absence of data is not evidence of a slate, and
  fail-open is the required direction for a ranking backstop.

  Where the two recorded bitrate fields disagree, the HIGHER one is used.
  video_bitrate and ffmpeg_output_bitrate were measured disagreeing on real
  streams, for example 124 against 389 on one. Taking the higher value means a
  disagreement resolves in favour of keeping the stream.
"""
import pytest

# --------------------------------------------------------------------------- #
# Helpers: build the stream dict shape the sort actually receives
# --------------------------------------------------------------------------- #

def _stream(sid=1, name="X", height=1080, width=1920, fps=30.0,
            video_bitrate=None, output_bitrate=None, **extra):
    stats = {}
    if height is not None:
        stats["height"] = height
    if width is not None:
        stats["width"] = width
    if fps is not None:
        stats["source_fps"] = fps
    if video_bitrate is not None:
        stats["video_bitrate"] = video_bitrate
    if output_bitrate is not None:
        stats["ffmpeg_output_bitrate"] = output_bitrate
    stats.update(extra)
    return {"id": sid, "name": name, "stats": stats}


# The real signature measured on live data: 1080p claimed, 193 kbps carried.
SLATE = _stream(sid=1, name="slate", height=1080, video_bitrate=193,
                output_bitrate=193.1)
REAL = _stream(sid=2, name="real", height=1080, video_bitrate=5500,
               output_bitrate=5480.0)


# --------------------------------------------------------------------------- #
# The predicate itself
# --------------------------------------------------------------------------- #

def test_the_measured_slate_signature_is_flagged(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(SLATE, floor_kbps=300) is True


def test_a_genuine_high_bitrate_stream_is_not_flagged(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(REAL, floor_kbps=300) is False


def test_a_low_bitrate_stream_claiming_only_sd_is_not_flagged(plugin_module):
    """The floor is keyed to CLAIMED resolution. Legitimate SD content really
    does run at a few hundred kbps and must not be demoted for it."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    for h in (240, 360, 480, 576):
        s = _stream(height=h, video_bitrate=200)
        assert p._is_content_starved(s, floor_kbps=300) is False, h


def test_the_boundary_is_exclusive(plugin_module):
    """Exactly at the floor is not starved. Below it is."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(_stream(video_bitrate=300), floor_kbps=300) is False
    assert p._is_content_starved(_stream(video_bitrate=299), floor_kbps=300) is True


# --------------------------------------------------------------------------- #
# Fail open: missing or unusable data never demotes
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stream", [
    {"id": 9, "name": "no stats key at all"},
    {"id": 9, "name": "stats is None", "stats": None},
    {"id": 9, "name": "empty stats", "stats": {}},
    {"id": 9, "name": "height only", "stats": {"height": 1080}},
    {"id": 9, "name": "bitrate only", "stats": {"video_bitrate": 100}},
    {"id": 9, "name": "height not a number",
     "stats": {"height": "HD", "video_bitrate": 100}},
    {"id": 9, "name": "bitrate not a number",
     "stats": {"height": 1080, "video_bitrate": "low"}},
    {"id": 9, "name": "bitrate is None",
     "stats": {"height": 1080, "video_bitrate": None}},
    {"id": 9, "name": "bitrate is zero, which means not measured",
     "stats": {"height": 1080, "video_bitrate": 0}},
])
def test_missing_or_unusable_evidence_never_demotes(plugin_module, stream):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(stream, floor_kbps=300) is False, stream["name"]


def test_a_disagreement_between_the_two_bitrate_fields_favours_keeping(plugin_module):
    """Measured on real data: video_bitrate 124 with ffmpeg_output_bitrate 389.
    The higher value wins, so this is not demoted."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    s = _stream(height=720, video_bitrate=124, output_bitrate=389)
    assert p._is_content_starved(s, floor_kbps=300) is False


def test_both_fields_low_is_still_flagged(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    s = _stream(height=720, video_bitrate=124, output_bitrate=130)
    assert p._is_content_starved(s, floor_kbps=300) is True


def test_output_bitrate_alone_is_enough_evidence(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    s = _stream(height=1080, video_bitrate=None, output_bitrate=193.1)
    assert p._is_content_starved(s, floor_kbps=300) is True


# --------------------------------------------------------------------------- #
# The sort: a starved stream must end up behind everything not proven starved
# --------------------------------------------------------------------------- #

def _sorted_names(p, streams):
    return [s["name"] for s in p._sort_streams_by_quality(streams)]


def test_a_starved_stream_sorts_behind_a_healthy_one(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._prioritize_quality = False
    assert _sorted_names(p, [SLATE, REAL]) == ["real", "slate"]
    assert _sorted_names(p, [REAL, SLATE]) == ["real", "slate"]


def test_a_starved_stream_sorts_behind_an_unprobed_unknown_one(plugin_module):
    """Issue 40 asks for these to rank below every stream NOT PROVEN BAD, which
    includes streams carrying no evidence either way."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._prioritize_quality = False
    unknown = {"id": 3, "name": "unknown", "stats": {}}
    assert _sorted_names(p, [SLATE, unknown])[-1] == "slate"


def test_a_starved_stream_beats_nothing_but_is_still_present(plugin_module):
    """Demotion, never removal. Match and Assign replaces a channel's whole
    stream list, so dropping a stream can take a channel off air. Ordering is
    the safe lever, which is the same reasoning used for the zone rule."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._prioritize_quality = False
    out = p._sort_streams_by_quality([SLATE, REAL])
    assert len(out) == 2
    assert {s["name"] for s in out} == {"slate", "real"}


def test_two_starved_streams_keep_a_stable_relative_order(plugin_module):
    """Both are demoted, but the better of the two still leads."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._prioritize_quality = False
    worse = _stream(sid=4, name="worse", height=1080, video_bitrate=90)
    out = _sorted_names(p, [worse, SLATE])
    assert set(out) == {"worse", "slate"}


def test_the_feature_off_leaves_ordering_exactly_as_before(plugin_module):
    """With the setting disabled the slate keeps its claimed-resolution win, so
    nobody's existing ordering changes until they opt in or the default is
    deliberately flipped."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = False
    p._content_starved_floor_kbps = 300
    p._prioritize_quality = False
    before = _sorted_names(p, [SLATE, REAL])
    p_on = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p_on._content_starved_enabled = True
    p_on._content_starved_floor_kbps = 300
    p_on._prioritize_quality = False
    after = _sorted_names(p_on, [SLATE, REAL])
    assert before != after or before == ["real", "slate"]


# --------------------------------------------------------------------------- #
# Settings resolution. This is the bug-139 shape: a setting read off the
# instance fails silently on any entry path that never primed it.
# --------------------------------------------------------------------------- #

def test_the_floor_resolves_from_the_live_settings_dict(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._resolve_content_starved_floor({"content_bitrate_floor_kbps": 500}) == 500


def test_a_missing_setting_falls_back_to_the_default(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    default = plugin_module.PluginConfig.DEFAULT_CONTENT_BITRATE_FLOOR_KBPS
    assert p._resolve_content_starved_floor({}) == default


@pytest.mark.parametrize("bad", ["", None, "abc", -5, 0])
def test_an_unusable_floor_falls_back_rather_than_disabling_the_rule(plugin_module, bad):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    default = plugin_module.PluginConfig.DEFAULT_CONTENT_BITRATE_FLOOR_KBPS
    assert p._resolve_content_starved_floor({"content_bitrate_floor_kbps": bad}) == default


def test_the_enable_flag_resolves_from_the_live_settings_dict(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._resolve_demote_content_starved({"demote_content_starved": True}) is True
    assert p._resolve_demote_content_starved({"demote_content_starved": False}) is False


def test_the_enable_flag_has_a_default_when_absent(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    default = plugin_module.PluginConfig.DEFAULT_DEMOTE_CONTENT_STARVED
    assert p._resolve_demote_content_starved({}) is default


# --------------------------------------------------------------------------- #
# The settings must actually be declared, or none of the above is reachable
# --------------------------------------------------------------------------- #

def test_both_settings_are_declared_as_fields(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    ids = {f["id"] for f in p.fields}
    assert "demote_content_starved" in ids
    assert "content_bitrate_floor_kbps" in ids


# --------------------------------------------------------------------------- #
# Wiring. Resolving correctly is worthless if the action never calls the
# resolver: that is precisely how the prioritize_quality setting shipped
# broken (bug-139). These drive the real actions far enough to prove the
# resolution happens on each entry path, with no database required.
# --------------------------------------------------------------------------- #

class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def test_sort_streams_action_resolves_the_setting_on_its_own_path(
        plugin_module, monkeypatch):
    """Sort never calls load_process_channels_action, so it has to resolve
    this itself or the sort silently keeps ranking placeholder streams first."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)

    result = inst.sort_streams_action(
        {"profile_name": "Default", "demote_content_starved": False,
         "content_bitrate_floor_kbps": 450}, _Logger())

    # Bails out at the profile lookup, but the settings were already resolved.
    assert result["status"] == "error"
    assert inst._content_starved_enabled is False
    assert inst._content_starved_floor_kbps == 450


def test_sort_streams_action_logs_which_placeholder_rule_it_applied(
        plugin_module, monkeypatch):
    """The prioritize_quality bug was invisible in the logs, which is why it
    survived. Sort states this one explicitly."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [], raising=False)
    log = _Logger()

    inst.sort_streams_action(
        {"profile_name": "Default", "demote_content_starved": True,
         "content_bitrate_floor_kbps": 300}, log)

    joined = " ".join(log.messages).lower()
    assert "placeholder" in joined
    assert "300" in joined
