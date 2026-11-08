"""A placeholder must not hide behind a bitrate stored before it became one.

Dispatcharr updates resolution, audio fields and ffmpeg_output_bitrate when a
stream plays, and leaves video_bitrate alone when the source does not report
one. A channel that was healthy and then started serving the provider's dead
placeholder file therefore holds a mix: placeholder audio and output bitrate
beside the video_bitrate of the real broadcast. The higher-field rule in
_is_content_starved then reads the old value and keeps the placeholder first.

Measured 2026-11-08 across 463 stored streams of 720p or more with both fields:

  419 normal streams: video_bitrate at most 3.1 times the output bitrate.
  4 placeholder streams: 4.2 to 23 times, with 44100 Hz and 44 kbit/s audio.
  3 real streams passed by IPTV Checker: 5.6 to 7.8 times, output near 193,
  audio about 258 kbit/s at 48000 Hz. Their OUTPUT value is the old one, so a
  ratio alone would demote working streams. The audio format separates them.
"""
import pytest


def _stream(name, video, output, sample_rate=44100, audio=44.0,
            resolution="1920x1080", fps=30.0):
    stats = {"resolution": resolution, "source_fps": fps,
             "video_bitrate": video, "ffmpeg_output_bitrate": output}
    if sample_rate is not None:
        stats["sample_rate"] = sample_rate
    if audio is not None:
        stats["audio_bitrate"] = audio
    return {"id": 1, "name": name, "stats": stats}


@pytest.mark.parametrize("video,output", [
    (5916.0, 253.1),
    (4933.0, 253.0),
    (1081.0, 253.3),
    (1056.0, 250.2),
])
def test_measured_placeholders_with_an_old_video_bitrate_are_flagged(
        plugin_module, video, output):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(_stream("dead", video, output),
                                 floor_kbps=300) is True


@pytest.mark.parametrize("video,output,audio", [
    (1504.0, 192.6, 258.662),
    (1504.0, 192.8, 258.662),
    (1087.0, 193.0, 258.53),
])
def test_real_streams_with_an_old_output_bitrate_are_kept(
        plugin_module, video, output, audio):
    """Measured: IPTV Checker read these as Alive on 2026-11-06."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    s = _stream("real", video, output, sample_rate=48000, audio=audio,
                resolution="1280x720", fps=25.0)
    assert p._is_content_starved(s, floor_kbps=300) is False


def test_ratio_at_the_limit_is_not_evidence(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(_stream("x", 1000.0, 250.0),
                                 floor_kbps=300) is False


def test_output_at_the_floor_is_not_evidence(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert p._is_content_starved(_stream("x", 5916.0, 300.0),
                                 floor_kbps=300) is False


@pytest.mark.parametrize("sample_rate,audio", [
    (None, 44.0),
    (44100, None),
    (48000, 44.0),
    (44100, 128.0),
    ("unknown", 44.0),
    (44100, "unknown"),
])
def test_incomplete_or_different_audio_never_demotes(
        plugin_module, sample_rate, audio):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    s = _stream("x", 5916.0, 253.1, sample_rate=sample_rate, audio=audio)
    assert p._is_content_starved(s, floor_kbps=300) is False


def test_the_shared_bitrate_reader_still_returns_the_higher_value(plugin_module):
    """The bitrate band tie-break also reads this helper and must not change."""
    stats = _stream("x", 5916.0, 253.1)["stats"]
    assert plugin_module.Plugin._stats_bitrate_kbps(stats) == 5916.0


def test_the_placeholder_sorts_behind_a_working_720p_source(plugin_module):
    """The live case: 1920x1080 outranked 1280x720 with quality sorting on."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    p._prioritize_quality = True
    dead = _stream("dead", 5916.0, 253.1)
    real = _stream("real", 3696.0, 3745.3, sample_rate=48000, audio=128.0,
                   resolution="1280x720", fps=29.97)
    names = [s["name"] for s in p._sort_streams_by_quality([dead, real])]
    assert names == ["real", "dead"]
