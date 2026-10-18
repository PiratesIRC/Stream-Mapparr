"""Fill Mirror Backups: the planning rules, without a database.

The operator's M3U accounts in one server group are mirrors of each other: the
same provider stream ids on different provider hosts (measured 2026-10-18,
three accounts with 5,924 streams each and every id shared). A channel that
holds a stream from one host should also hold the mirror of that stream from
each other host in the group, so it has a backup when one host fails.

Rules the operator approved:
  - the mirror is found by provider stream id, never by name (names drift
    between mirrors: 5,031 of 5,924 matched between two accounts);
  - only ACTIVE accounts are added from;
  - a mirror is added only for a host the channel has no stream from yet,
    so two accounts on one host never both get added;
  - add only, never remove; new streams go after the last provider stream
    and before trailing streams with no provider host (the custom
    placeholder stream).
"""
import pytest

from mirror_backups import host_key, plan_mirror_fill


HOST_A = "a.example"
HOST_B = "b.example"
HOST_C = "c.example"

ACCOUNTS = {
    1: {"name": "custom", "group_id": None, "active": True, "host": None},
    6: {"name": "acct6", "group_id": 1, "active": True, "host": HOST_A},
    7: {"name": "acct7", "group_id": 1, "active": True, "host": HOST_B},
    8: {"name": "acct8", "group_id": 1, "active": True, "host": HOST_A},
    9: {"name": "acct9", "group_id": 1, "active": False, "host": HOST_C},
}


def _s(pk, account_id, provider_id):
    return {"id": pk, "account_id": account_id, "provider_id": provider_id}


def _index(*rows):
    return {(acct, pid): pk for acct, pid, pk in rows}


# --------------------------------------------------------------------------- #
# host_key
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("url,expected", [
    ("http://Host.Example:8080/get.php?u=x", "host.example:8080"),
    ("http://host.example", "host.example"),
    ("https://user:pw@host.example/path", "host.example"),
    ("", None),
    (None, None),
    ("not a url", None),
])
def test_host_key_reduces_a_server_url_to_host_and_port(url, expected):
    assert host_key(url) == expected


def test_host_key_treats_a_different_port_as_a_different_server():
    assert host_key("http://h.example:80/") != host_key("http://h.example:8080/")


# --------------------------------------------------------------------------- #
# plan_mirror_fill
# --------------------------------------------------------------------------- #
def test_a_channel_with_one_host_gets_the_mirror_from_the_other_host():
    channels = {100: [_s(10, 6, 555), _s(99, 1, None)]}
    index = _index((7, 555, 20), (8, 555, 30))
    plan = plan_mirror_fill(channels, ACCOUNTS, index)
    assert len(plan) == 1
    entry = plan[0]
    assert entry["channel_id"] == 100
    assert [a["stream_id"] for a in entry["added"]] == [20]
    assert entry["added"][0]["account_id"] == 7
    assert entry["added"][0]["source_stream_id"] == 10
    # Inserted after the provider stream, before the custom placeholder.
    assert entry["old_order"] == [10, 99]
    assert entry["new_order"] == [10, 20, 99]


def test_an_account_on_a_host_the_channel_already_has_is_not_added():
    """Accounts 6 and 8 share a host: holding 6 means 8 adds nothing."""
    channels = {100: [_s(10, 6, 555)]}
    index = _index((8, 555, 30))
    assert plan_mirror_fill(channels, ACCOUNTS, index) == []


def test_only_one_mirror_is_added_per_missing_host():
    """Starting from host B, hosts A has two accounts; only one is added."""
    channels = {100: [_s(20, 7, 555)]}
    index = _index((6, 555, 10), (8, 555, 30))
    plan = plan_mirror_fill(channels, ACCOUNTS, index)
    assert [a["stream_id"] for a in plan[0]["added"]] == [10]


def test_inactive_accounts_are_never_added_from():
    channels = {100: [_s(20, 7, 555)]}
    index = _index((9, 555, 40))
    assert plan_mirror_fill(channels, ACCOUNTS, index) == []


def test_a_stream_from_an_inactive_account_does_not_count_as_a_backup():
    """Account 9 is off, so its stream plays nothing; host B is still missing."""
    channels = {100: [_s(10, 6, 555), _s(40, 9, 555)]}
    index = _index((7, 555, 20))
    plan = plan_mirror_fill(channels, ACCOUNTS, index)
    assert [a["stream_id"] for a in plan[0]["added"]] == [20]
    assert plan[0]["new_order"] == [10, 40, 20]


def test_a_stream_from_an_inactive_account_can_still_name_the_mirror():
    channels = {100: [_s(40, 9, 555)]}
    index = _index((6, 555, 10), (7, 555, 20))
    plan = plan_mirror_fill(channels, ACCOUNTS, index)
    assert [a["stream_id"] for a in plan[0]["added"]] == [10, 20]


def test_a_channel_already_covered_on_every_active_host_is_left_alone():
    channels = {100: [_s(10, 6, 555), _s(20, 7, 555), _s(99, 1, None)]}
    index = _index((6, 555, 10), (7, 555, 20), (8, 555, 30))
    assert plan_mirror_fill(channels, ACCOUNTS, index) == []


def test_a_channel_with_only_a_custom_stream_has_nothing_to_mirror():
    channels = {100: [_s(99, 1, None)]}
    assert plan_mirror_fill(channels, ACCOUNTS, _index((7, 555, 20))) == []


def test_a_mirror_missing_from_the_other_account_adds_nothing():
    channels = {100: [_s(10, 6, 555)]}
    assert plan_mirror_fill(channels, ACCOUNTS, _index((7, 999, 20))) == []


def test_a_source_without_a_provider_stream_id_is_skipped():
    channels = {100: [_s(10, 6, None)]}
    assert plan_mirror_fill(channels, ACCOUNTS, _index((7, None, 20))) == []


def test_the_first_stream_on_the_channel_names_the_mirror():
    """The first stream is the one Sort ranked best, so its mirror is the one wanted."""
    channels = {100: [_s(10, 6, 555), _s(11, 6, 777)]}
    index = _index((7, 555, 20), (7, 777, 21))
    plan = plan_mirror_fill(channels, ACCOUNTS, index)
    assert [a["stream_id"] for a in plan[0]["added"]] == [20]
    assert plan[0]["new_order"] == [10, 11, 20]


def test_a_mirror_already_on_the_channel_is_not_added_twice():
    """Its host counts as present, so nothing is added and nothing duplicates."""
    channels = {100: [_s(10, 6, 555), _s(20, 7, 555)]}
    index = _index((7, 555, 20))
    assert plan_mirror_fill(channels, ACCOUNTS, index) == []


def test_mirrors_come_only_from_the_source_accounts_server_group():
    accounts = dict(ACCOUNTS)
    accounts[12] = {"name": "other", "group_id": 2, "active": True, "host": HOST_C}
    channels = {100: [_s(10, 6, 555), _s(20, 7, 555)]}
    index = _index((12, 555, 50))
    assert plan_mirror_fill(channels, accounts, index) == []


def test_an_account_outside_any_server_group_is_never_a_source():
    accounts = dict(ACCOUNTS)
    accounts[13] = {"name": "loose", "group_id": None, "active": True, "host": HOST_C}
    channels = {100: [_s(60, 13, 555)]}
    index = _index((7, 555, 20))
    assert plan_mirror_fill(channels, accounts, index) == []


def test_a_stream_whose_account_is_unknown_is_kept_where_it_is():
    channels = {100: [_s(10, 6, 555), _s(98, None, None)]}
    index = _index((7, 555, 20))
    plan = plan_mirror_fill(channels, ACCOUNTS, index)
    assert plan[0]["new_order"] == [10, 20, 98]


def test_the_plan_is_ordered_by_channel_id_and_calls_tick_per_channel():
    channels = {200: [_s(11, 6, 777)], 100: [_s(10, 6, 555)]}
    index = _index((7, 555, 20), (7, 777, 21))
    ticks = []
    plan = plan_mirror_fill(channels, ACCOUNTS, index, tick=lambda: ticks.append(1))
    assert [e["channel_id"] for e in plan] == [100, 200]
    assert len(ticks) == 2


def test_the_plan_never_removes_a_stream():
    channels = {100: [_s(10, 6, 555), _s(40, 9, 555), _s(99, 1, None)]}
    index = _index((7, 555, 20))
    entry = plan_mirror_fill(channels, ACCOUNTS, index)[0]
    assert set(entry["old_order"]) <= set(entry["new_order"])
    assert len(entry["new_order"]) == len(set(entry["new_order"]))
