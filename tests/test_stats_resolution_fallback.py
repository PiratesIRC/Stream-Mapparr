"""Stream stats that carry only `resolution` must rank by that resolution.

Reported 2026-10-18 (subzero79, Discord). Dispatcharr's own stream preview
writes `resolution` as a "WIDTHxHEIGHT" string into Stream.stream_stats and
never writes `width` or `height` (channel_service.py, _update_stream_stats_in_db
call). IPTV Checker writes all three. The quality sort read only `width` and
`height`, so a stream previewed in Dispatcharr fell into the no-stats branch
(tier 3) and ranked behind an SD stream IPTV Checker had checked (tier 2).

Reporter's channel: Sky Sports Premier League, SD 1024x576 at 25 fps with
width/height ahead of FHD 1920x1080 at 50 fps with only `resolution`.

The same gap switched off placeholder demotion (it reads `height`) and zeroed
the throughput nominal-bitrate estimate for those streams.
"""
import pytest


def _bare(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst._content_starved_enabled = False
    return inst


@pytest.mark.parametrize(
    "stats,expected",
    [
        ({"width": 1024, "height": 576}, (1024, 576)),
        ({"resolution": "1920x1080"}, (1920, 1080)),
        ({"resolution": " 1280 X 720 "}, (1280, 720)),
        ({"width": 0, "height": 0, "resolution": "1920x1080"}, (1920, 1080)),
        ({"width": "1280", "height": "720"}, (1280, 720)),
        ({"resolution": "1920x1080 [SAR 1:1 DAR 16:9]"}, (1920, 1080)),
        ({"resolution": None}, (0, 0)),
        ({"resolution": "garbage"}, (0, 0)),
        ({"resolution": "1920x"}, (0, 0)),
        ({"resolution": 1080}, (0, 0)),
        ({"height": 1080}, (0, 1080)),
        ({"width": "abc", "height": 720}, (0, 720)),
        ({}, (0, 0)),
        (None, (0, 0)),
        ("not a dict", (0, 0)),
    ],
)
def test_stats_dimensions(plugin_module, stats, expected):
    assert plugin_module.Plugin._stats_dimensions(stats) == expected


def test_resolution_only_fhd_ranks_above_width_height_sd(plugin_module):
    inst = _bare(plugin_module)
    inst._prioritize_quality = True
    streams = [
        {"id": 1, "name": "SD", "_m3u_priority": 0,
         "stats": {"width": 1024, "height": 576, "resolution": "1024x576", "source_fps": 25}},
        {"id": 2, "name": "FHD", "_m3u_priority": 0,
         "stats": {"resolution": "1920x1080", "source_fps": 50}},
        {"id": 3, "name": "HD", "_m3u_priority": 0,
         "stats": {"resolution": "1280x720", "source_fps": 50}},
    ]
    order = [s["id"] for s in inst._sort_streams_by_quality(streams)]
    assert order == [2, 3, 1]


def test_content_starved_reads_resolution_when_height_absent(plugin_module):
    slate = {"id": 1, "name": "X",
             "stats": {"resolution": "1920x1080", "video_bitrate": 150}}
    assert plugin_module.Plugin._is_content_starved(slate, floor_kbps=300) is True


def test_throughput_nominal_uses_resolution(plugin_module):
    P = plugin_module.Plugin
    from_res = P._estimate_nominal_bitrate(*P._stats_dimensions({"resolution": "1920x1080"}), 50)
    from_wh = P._estimate_nominal_bitrate(1920, 1080, 50)
    assert from_res == from_wh
    assert from_res != plugin_module.PluginConfig.NOMINAL_BITRATE_FALLBACK_MBPS
