"""Tests for _epg_next_event_date_is_today: guards against a "Next Event: X at
TIME on DATE" placeholder staying the CURRENTLY ACTIVE ProgramData row for days
(live data: 84 such rows active at once, most dated a week-plus out) and being
reported as though the event were happening today.
"""
from datetime import datetime, timedelta, timezone as stdlib_tz


def _bare_plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def _date_label(dt):
    return f"{dt.strftime('%b')} {dt.day}"


def test_no_date_clause_is_today(plugin_module):
    """A live, unwrapped title (no 'at TIME on DATE' clause) has nothing to
    validate -- that's exactly what a real live event looks like once the
    provider flips it from the waiting placeholder."""
    p = _bare_plugin(plugin_module)
    assert p._epg_next_event_date_is_today("WWE MONDAY NIGHT RAW") is True
    assert p._epg_next_event_date_is_today("No Event Today") is True
    assert p._epg_next_event_date_is_today("") is True
    assert p._epg_next_event_date_is_today(None) is True


def test_todays_date_is_today(plugin_module):
    today = datetime.now(stdlib_tz.utc)
    title = f"Next Event: WWE MONDAY NIGHT RAW at 05:00PM on {_date_label(today)}"
    p = _bare_plugin(plugin_module)
    assert p._epg_next_event_date_is_today(title) is True


def test_future_date_is_not_today(plugin_module):
    """The real bug this guards: a placeholder dated a week-plus out (confirmed
    live: 'Next Event: AEW Dynamite: Grand Slam Mexico at 05:00PM on Aug 5' still
    active as the current row on Jul 31) must not be reported as today's event."""
    future = datetime.now(stdlib_tz.utc) + timedelta(days=9)
    title = f"Next Event: AEW Dynamite: Grand Slam Mexico at 05:00PM on {_date_label(future)}"
    p = _bare_plugin(plugin_module)
    assert p._epg_next_event_date_is_today(title) is False


def test_lowercase_am_pm_still_matches(plugin_module):
    today = datetime.now(stdlib_tz.utc)
    title = f"Next Event: Some Show at 05:00pm on {_date_label(today)}"
    p = _bare_plugin(plugin_module)
    assert p._epg_next_event_date_is_today(title) is True


def test_date_check_uses_configured_localtime(plugin_module, monkeypatch):
    """Confirms the check goes through _epg_local_now (Dispatcharr's
    user-configured timezone via _dispatcharr_timezone/CoreSettings), not
    Django's active timezone (django.utils.timezone.localtime, which only
    ever reflects settings.TIME_ZONE -- UTC in Dispatcharr -- since a plugin
    never sees the per-request timezone activation Django applies in the
    normal request path). Confirmed live: Django's active tz was UTC while
    CoreSettings.get_system_time_zone() was America/Phoenix, a 7-hour gap
    where the two calendar days disagree every single day."""
    fixed_local = datetime(2026, 8, 5, 10, 0, tzinfo=stdlib_tz.utc)
    p = _bare_plugin(plugin_module)
    monkeypatch.setattr(p, "_epg_local_now", lambda: fixed_local)
    title = "Next Event: AEW Dynamite: Grand Slam Mexico at 05:00PM on Aug 5"
    assert p._epg_next_event_date_is_today(title) is True
    title_wrong_day = "Next Event: AEW Dynamite: Grand Slam Mexico at 05:00PM on Aug 6"
    assert p._epg_next_event_date_is_today(title_wrong_day) is False


def test_epg_local_now_uses_dispatcharr_timezone(plugin_module, monkeypatch):
    """_epg_local_now itself: resolves through _dispatcharr_timezone (the
    CoreSettings-backed helper the scheduler already uses), not Django's
    active timezone."""
    p = _bare_plugin(plugin_module)
    monkeypatch.setattr(p, "_dispatcharr_timezone", lambda: "America/Phoenix")
    now = p._epg_local_now()
    assert str(now.tzinfo) == "America/Phoenix"


def test_epg_local_now_falls_back_to_utc_on_error(plugin_module, monkeypatch):
    p = _bare_plugin(plugin_module)
    monkeypatch.setattr(p, "_dispatcharr_timezone", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    now = p._epg_local_now()
    assert now.tzinfo is not None  # degrade-don't-fail: still a valid aware datetime


def test_full_month_name_matches_abbreviated(plugin_module, monkeypatch):
    """Review finding: the old string-equality check silently rejected
    'August 5' and 'Aug 05' even when today is 'Aug 5' -- a real date compare
    (month, day) fixes the whole class rather than one format at a time."""
    fixed = datetime(2026, 8, 5, 10, 0, tzinfo=stdlib_tz.utc)
    p = _bare_plugin(plugin_module)
    monkeypatch.setattr(p, "_epg_local_now", lambda: fixed)
    for date_text in ("Aug 5", "Aug 05", "August 5", "August 05"):
        title = f"Next Event: Some Show at 05:00PM on {date_text}"
        assert p._epg_next_event_date_is_today(title) is True, date_text


def test_unrecognized_date_format_is_permissive(plugin_module, monkeypatch, caplog):
    """A date clause that matches the regex shape but not a recognized month
    name (e.g. a non-English provider convention) degrades permissively
    (treated as today) rather than silently discarding the match forever --
    but it IS logged, unlike the old behavior, so the gap is discoverable."""
    import logging
    caplog.set_level(logging.DEBUG, logger="plugins.stream_mapparr")
    fixed = datetime(2026, 8, 5, 10, 0, tzinfo=stdlib_tz.utc)
    p = _bare_plugin(plugin_module)
    monkeypatch.setattr(p, "_epg_local_now", lambda: fixed)
    title = "Next Event: Some Show at 05:00PM on Zzz 5"  # matches the regex shape, not a real month
    assert p._epg_next_event_date_is_today(title) is True
    assert any("Zzz 5" in r.message for r in caplog.records)
