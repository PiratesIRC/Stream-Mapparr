"""A demoted placeholder stream has to be visible, not silent.

Issue 40 asked for a distinct label so an operator can see which streams were
treated as placeholders. Without one the demotion is invisible: a stream moves
to the back of a channel and nothing anywhere says why. That is the shape this
workspace has been bitten by before, where a rule works correctly and no one can
tell whether it fired, so nobody notices when it fires wrongly either.

Two surfaces carry the label:

  The CSV export written by the Sort Alternate Streams action already has a
  per-stream `tiers` column aligned with `stream_names`. A placeholder stream is
  marked there, so the CSV shows position, name and reason together.

  The emailed report built by reports.py marks the stream name itself, because
  that report has no per-stream column to put a flag in.

The marker is a plain ASCII word rather than a symbol, because the CSV is opened
in spreadsheets and the report is emailed, and neither is a good place for
characters that may not survive an encoding round trip.
"""
import pytest
from reports import build_model, render_csv, render_html

PLACEHOLDER = "placeholder"


# --------------------------------------------------------------------------- #
# The tier label used by the CSV export
# --------------------------------------------------------------------------- #

def _stream(sid, name, height=None, video_bitrate=None):
    stats = {}
    if height is not None:
        stats["height"] = height
    if video_bitrate is not None:
        stats["video_bitrate"] = video_bitrate
    return {"id": sid, "name": name, "stats": stats}


def test_a_placeholder_stream_gets_the_placeholder_tier(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    s = _stream(1, "slate", height=1080, video_bitrate=193)
    assert p._stream_tier_label(s, {}, 1.10) == PLACEHOLDER


def test_a_normal_stream_keeps_its_throughput_tier(plugin_module):
    """The placeholder marker replaces the throughput tier only when the stream
    is proven starved. Everything else reports as before."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    s = _stream(2, "real", height=1080, video_bitrate=5500)
    assert p._stream_tier_label(s, {}, 1.10) == "unknown"


def test_the_label_is_not_applied_when_the_setting_is_off(plugin_module):
    """A user who turned the rule off must not see streams described as
    placeholders in the export, because nothing was demoted."""
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = False
    p._content_starved_floor_kbps = 300
    s = _stream(1, "slate", height=1080, video_bitrate=193)
    assert p._stream_tier_label(s, {}, 1.10) != PLACEHOLDER


def test_a_stream_with_no_evidence_is_never_labelled(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._content_starved_enabled = True
    p._content_starved_floor_kbps = 300
    assert p._stream_tier_label({"id": 3, "name": "x"}, {}, 1.10) != PLACEHOLDER


def test_the_marker_is_plain_ascii(plugin_module):
    """The CSV is opened in spreadsheets and the report is emailed. Neither is a
    safe place for a character that may not survive an encoding round trip."""
    assert PLACEHOLDER.isascii()
    assert plugin_module.PluginConfig.CONTENT_STARVED_LABEL.isascii()
    assert plugin_module.PluginConfig.CONTENT_STARVED_LABEL == PLACEHOLDER


# --------------------------------------------------------------------------- #
# The emailed report
# --------------------------------------------------------------------------- #

ACCOUNTS = ["provider-one", "provider-two"]


def _model(names, flagged=None):
    return build_model(
        [{"channel_name": "Sky News", "stream_names": list(names),
          "placeholder_streams": list(flagged or [])}],
        ACCOUNTS, {}, 0)


def test_a_flagged_stream_is_marked_in_the_report_model():
    model = _model(["good feed", "slate feed"], flagged=["slate feed"])
    blob = repr(model)
    assert PLACEHOLDER in blob


def test_an_unflagged_stream_is_not_marked():
    model = _model(["good feed", "other feed"])
    assert PLACEHOLDER not in repr(model)


def test_the_marker_survives_into_the_csv_rendering():
    csv_text = render_csv(_model(["good feed", "slate feed"], flagged=["slate feed"]))
    assert PLACEHOLDER in csv_text
    assert "good feed" in csv_text


def test_the_marker_survives_into_the_html_rendering():
    html = render_html(_model(["good feed", "slate feed"], flagged=["slate feed"]))
    assert PLACEHOLDER in html


def test_a_report_built_without_the_key_still_works():
    """Every existing caller omits placeholder_streams. Absent must behave the
    same as empty rather than raising, or an ordinary run breaks."""
    model = build_model([{"channel_name": "Sky News",
                          "stream_names": ["a", "b"]}], ACCOUNTS, {}, 0)
    assert PLACEHOLDER not in repr(model)


def test_marking_does_not_defeat_the_account_name_sanitiser():
    """The report sanitiser removes an M3U account name from a stream label.
    Marking must happen in a way that leaves that intact, because the account
    name is the provider hostname and must never reach an email."""
    model = build_model(
        [{"channel_name": "Sky News",
          "stream_names": ["slate feed (provider-one)"],
          "placeholder_streams": ["slate feed (provider-one)"]}],
        ACCOUNTS, {}, 0)
    blob = repr(model)
    assert "provider-one" not in blob
    assert PLACEHOLDER in blob


@pytest.mark.parametrize("flagged", [None, [], ["not present in the list"]])
def test_a_flag_list_that_matches_nothing_is_harmless(flagged):
    model = _model(["a", "b"], flagged=flagged)
    assert PLACEHOLDER not in repr(model)


def test_the_two_modules_agree_on_the_marker(plugin_module):
    """plugin.py writes the marker into the CSV export and reports.py writes it
    into the emailed report. They are separate constants in separate modules, so
    a change to one without the other would silently produce two different words
    for the same thing."""
    import reports
    assert plugin_module.PluginConfig.CONTENT_STARVED_LABEL == reports.PLACEHOLDER_LABEL


# --------------------------------------------------------------------------- #
# Wiring. reports.py can mark a stream, but only if the action that builds the
# report input actually supplies the flagged names. Marking support with no
# producer is dead code that looks finished.
# --------------------------------------------------------------------------- #

def test_the_match_and_assign_action_supplies_the_flag_to_the_report(plugin_module):
    """The report input is built inside add_streams_to_channels_action. This
    reads that source rather than driving the whole action, which needs a
    database. A source check is weak on its own, so the behaviour of the
    marking itself is covered by the tests above."""
    import inspect
    src = inspect.getsource(plugin_module.Plugin.add_streams_to_channels_action)
    assert "placeholder_streams" in src, (
        "the report input does not carry placeholder_streams, so reports.py "
        "can never mark anything in production")


def test_the_sort_action_supplies_the_flag_to_the_csv_export(plugin_module):
    import inspect
    src = inspect.getsource(plugin_module.Plugin.sort_streams_action)
    assert "_stream_tier_label" in src, (
        "the CSV export is not using the tier label helper, so a demoted "
        "placeholder is not marked in the export")
