"""Tests for _epg_next_event_date_is_today: guards against a "Next Event: X at
TIME on DATE" placeholder staying the CURRENTLY ACTIVE ProgramData row for days
(live data: 84 such rows active at once, most dated a week-plus out) and being
reported as though the event were happening today.
"""
from datetime import datetime, timedelta, timezone as stdlib_tz

import pytest


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
    """Confirms the check goes through timezone.localtime (the configured/active
    timezone), not a raw UTC now() -- matters near local midnight, where the UTC
    calendar day and the configured-timezone calendar day can disagree."""
    fixed_local = datetime(2026, 8, 5, 10, 0, tzinfo=stdlib_tz.utc)
    monkeypatch.setattr(plugin_module.timezone, "localtime", lambda *a, **k: fixed_local)
    title = "Next Event: AEW Dynamite: Grand Slam Mexico at 05:00PM on Aug 5"
    p = _bare_plugin(plugin_module)
    assert p._epg_next_event_date_is_today(title) is True
    title_wrong_day = "Next Event: AEW Dynamite: Grand Slam Mexico at 05:00PM on Aug 6"
    assert p._epg_next_event_date_is_today(title_wrong_day) is False
