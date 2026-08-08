"""Tests for EPG event watch: force-including a real, permanently-named stream
(e.g. "ESPN") as an alternate for another channel when its CURRENT EPG programme
title contains that channel's full name -- for events that never get their own
dedicated placeholder stream (see _collect_epg_watch_streams docstring).

_resolve_current_epg_title_for_stream touches Django ORM models (EPGData /
ProgramData) that conftest only stubs with bare MagicMocks, so these tests
monkeypatch it directly to return canned titles -- that keeps the test focused
on the actual new logic (the watch-name filter and the token-containment gate)
rather than re-testing EPG lookup plumbing the rest of the suite doesn't cover
either.
"""


def _bare_plugin(plugin_module, matcher):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = matcher()
    return p


def _epg_settings(watch_names):
    return {
        'enabled': True,
        'watch_source_stream_names': {n.lower() for n in watch_names},
        'cleanup_rules': [],
        'skip_titles': set(),
    }


IGNORE_ARGS = (None, True, True, True, True)  # ignore_tags, quality, regional, geographic, misc


def test_full_token_containment_matches(plugin_module, matcher, monkeypatch):
    """The real, confirmed-live case: ESPN's current programme title is a promo
    wrapper around the event name ('WWE SummerSlam Special'), not an exact
    match -- containment of every cleaned channel token is what should pass."""
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: "WWE SummerSlam Special")
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["ESPN"]), *IGNORE_ARGS)
    assert hits == streams


def test_missing_token_does_not_match(plugin_module, matcher, monkeypatch):
    """'SummerSlam 2026 Kickoff' drops the 'WWE' token -- confirmed live as a
    real but weaker guide entry that should NOT be treated as the same event."""
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: "SummerSlam 2026 Kickoff")
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["ESPN"]), *IGNORE_ARGS)
    assert hits == []


def test_unrelated_wordplay_coincidence_does_not_match(plugin_module, matcher, monkeypatch):
    """Regression guard for the false positive found while building this: a
    totally unrelated show ('Manitoba summer slam') sharing two words with the
    target channel must NOT match once the full token set (incl. 'wwe') is
    required."""
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: "Drop Zone: Manitoba summer slam Pt.3")
    streams = [{'name': 'Outdoor Channel', 'tvg_id': 'OutdoorChannel.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["Outdoor Channel"]), *IGNORE_ARGS)
    assert hits == []


def test_stream_not_in_watch_list_is_ignored(plugin_module, matcher, monkeypatch):
    """Even a perfectly matching title is irrelevant unless the stream's exact
    name was opted into the watch list -- this feature must never silently
    hijack streams the user didn't explicitly name."""
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: "WWE SummerSlam Special")
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["ESPN 2"]), *IGNORE_ARGS)  # different stream watched
    assert hits == []


def test_watch_name_matching_is_case_insensitive(plugin_module, matcher, monkeypatch):
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: "WWE SummerSlam Special")
    streams = [{'name': 'espn', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["ESPN"]), *IGNORE_ARGS)
    assert hits == streams


def test_empty_watch_list_returns_empty(plugin_module, matcher, monkeypatch):
    p = _bare_plugin(plugin_module, matcher)
    called = []
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: called.append(1) or "WWE SummerSlam Special")
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings([]), *IGNORE_ARGS)
    assert hits == []
    assert not called  # short-circuits before ever resolving EPG data


def test_no_current_programme_excludes_stream(plugin_module, matcher, monkeypatch):
    """A watched stream with no currently-airing title (idle slot, skip-title
    filtered upstream, or lookup failure) contributes nothing rather than
    erroring -- same degrade-don't-fail contract as the rest of the EPG path."""
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream', lambda *a, **k: None)
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["ESPN"]), *IGNORE_ARGS)
    assert hits == []


def test_single_token_channel_name_is_rejected(plugin_module, matcher, monkeypatch):
    """Review note: a single-token channel name ("Max", "Live", "News") would
    force-include a watched stream on almost any current programme -- the same
    shape of over-match this file already hit once (a shared idle EPG title
    over-matched 130 streams) arriving from a different angle. A channel name
    that cleans down to fewer than _EPG_WATCH_MIN_CHANNEL_TOKENS tokens is
    rejected before the containment check even runs."""
    p = _bare_plugin(plugin_module, matcher)
    called = []
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: called.append(1) or "Max Power Hour")
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "Max", streams, _epg_settings(["ESPN"]), *IGNORE_ARGS)
    assert hits == []
    assert not called  # short-circuits before ever resolving EPG data


def test_two_token_channel_name_is_allowed(plugin_module, matcher, monkeypatch):
    """The floor is >= 2 tokens, not > 1 word -- a genuine two-word channel
    name still works normally."""
    p = _bare_plugin(plugin_module, matcher)
    monkeypatch.setattr(p, '_resolve_current_epg_title_for_stream',
                         lambda *a, **k: "WWE SummerSlam Special")
    streams = [{'name': 'ESPN', 'tvg_id': 'ESPN.us'}]
    hits = p._collect_epg_watch_streams(
        "WWE SummerSlam", streams, _epg_settings(["ESPN"]), *IGNORE_ARGS)
    assert hits == streams
