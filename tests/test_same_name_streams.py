"""Tests for the opt-in same-name-stream setting (bug-140).

Reporter (Spain, DAZN): four genuinely distinct FORMULA 1 feeds are all published
by one provider under the byte-identical name "DAZN F1". `_deduplicate_streams`
keys on (name, m3u_account), so three of the four were discarded before assignment
and the channel got 1 stream. The same export shows "Dazn Mundial" matching 4/4 —
same account, same group — purely because that provider numbered its feeds.

The (name, account) key came from issue #28, to keep identically-named streams from
DIFFERENT providers for failover. Its assumption — same name + same provider means a
true duplicate — does not hold for a provider that ships numbered-in-fact-but-not-in-
name mirrors. Note Dispatcharr's default `m3u_hash_key = 'url'` makes stream identity
the URL, so two rows in one account cannot share a URL: same name + same account
therefore already implies different URLs, i.e. distinct feeds.

Opt-in (`allow_same_name_streams`, default off) so existing users keep today's
collapse until they ask for the new behavior.
"""
import pytest


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def _s(sid, name, account, url):
    return {"id": sid, "name": name, "m3u_account": account, "url": url}


# The reporter's four FORMULA 1 feeds: one name, one account, four URLs.
def _dazn_f1():
    return [
        _s(1, "DAZN F1", 7, "http://vacaionesenelmar.xo.je/live/a/b/1001.ts"),
        _s(2, "DAZN F1", 7, "http://vacaionesenelmar.xo.je/live/a/b/1002.ts"),
        _s(3, "DAZN F1", 7, "http://vacaionesenelmar.xo.je/live/a/b/1003.ts"),
        _s(4, "DAZN F1", 7, "http://vacaionesenelmar.xo.je/live/a/b/1004.ts"),
    ]


# --------------------------------------------------------------------------- #
# The resolver
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "value,expected",
    [(True, True), (False, False), ("true", True), ("1", True), ("false", False), ("", False)],
)
def test_resolver_coercion(plugin_module, value, expected):
    inst = _bare(plugin_module)
    assert inst._resolve_allow_same_name_streams(
        {"allow_same_name_streams": value}
    ) is expected


def test_resolver_defaults_to_off(plugin_module):
    """Opt-in: an existing user who never touches the setting keeps today's behavior."""
    inst = _bare(plugin_module)
    assert inst._resolve_allow_same_name_streams({}) is False
    assert plugin_module.PluginConfig.DEFAULT_ALLOW_SAME_NAME_STREAMS is False


# --------------------------------------------------------------------------- #
# Default (off) — issue #28 behavior must be byte-for-byte preserved
# --------------------------------------------------------------------------- #

def test_off_collapses_same_name_same_account(plugin_module):
    inst = _bare(plugin_module)
    kept = inst._deduplicate_streams(_dazn_f1())
    assert [s["id"] for s in kept] == [1]


def test_off_keeps_same_name_across_different_accounts(plugin_module):
    """issue #28: one stream per provider survives for failover."""
    inst = _bare(plugin_module)
    streams = [
        _s(1, "DAZN F1", 7, "http://a.example/1.ts"),
        _s(2, "DAZN F1", 9, "http://b.example/1.ts"),
    ]
    assert [s["id"] for s in inst._deduplicate_streams(streams)] == [1, 2]


def test_off_is_unaffected_by_url(plugin_module):
    """With the flag off the URL must not enter the key at all."""
    inst = _bare(plugin_module)
    streams = [
        _s(1, "DAZN F1", 7, "http://a.example/1.ts"),
        _s(2, "DAZN F1", 7, "http://a.example/2.ts"),
    ]
    assert [s["id"] for s in inst._deduplicate_streams(streams)] == [1]


# --------------------------------------------------------------------------- #
# On — the reporter's case
# --------------------------------------------------------------------------- #

def test_on_keeps_all_four_distinct_feeds(plugin_module):
    inst = _bare(plugin_module)
    kept = inst._deduplicate_streams(_dazn_f1(), allow_same_name_streams=True)
    assert [s["id"] for s in kept] == [1, 2, 3, 4]


def test_on_still_collapses_true_duplicates(plugin_module):
    """Identical name AND account AND url is a real duplicate at any setting."""
    inst = _bare(plugin_module)
    streams = [
        _s(1, "DAZN F1", 7, "http://a.example/1.ts"),
        _s(2, "DAZN F1", 7, "http://a.example/1.ts"),
        _s(3, "DAZN F1", 7, "http://a.example/2.ts"),
    ]
    kept = inst._deduplicate_streams(streams, allow_same_name_streams=True)
    assert [s["id"] for s in kept] == [1, 3]


def test_on_preserves_sort_order(plugin_module):
    """Dedup must not reorder — it runs after _sort_streams_by_quality."""
    inst = _bare(plugin_module)
    streams = list(reversed(_dazn_f1()))
    kept = inst._deduplicate_streams(streams, allow_same_name_streams=True)
    assert [s["id"] for s in kept] == [4, 3, 2, 1]


def test_on_still_drops_nameless_streams(plugin_module):
    inst = _bare(plugin_module)
    streams = [_s(1, "", 7, "http://a.example/1.ts"), _s(2, "DAZN F1", 7, "http://a.example/2.ts")]
    kept = inst._deduplicate_streams(streams, allow_same_name_streams=True)
    assert [s["id"] for s in kept] == [2]


def test_on_degrades_to_old_behavior_when_url_is_absent(plugin_module):
    """Not every stream dict in the codebase is built from _get_all_streams.

    A dict with no 'url' key must not crash and must not let the flag silently
    turn every same-named stream into a distinct one on missing data.
    """
    inst = _bare(plugin_module)
    streams = [
        {"id": 1, "name": "DAZN F1", "m3u_account": 7},
        {"id": 2, "name": "DAZN F1", "m3u_account": 7},
    ]
    kept = inst._deduplicate_streams(streams, allow_same_name_streams=True)
    assert [s["id"] for s in kept] == [1]


# --------------------------------------------------------------------------- #
# Plumbing: the URL has to actually reach the dedup
# --------------------------------------------------------------------------- #

def test_settings_field_is_registered(plugin_module):
    inst = _bare(plugin_module)
    ids = {f["id"] for f in plugin_module.Plugin.fields.fget(inst)} \
        if isinstance(plugin_module.Plugin.fields, property) else \
        {f["id"] for f in plugin_module.Plugin.fields}
    assert "allow_same_name_streams" in ids


def test_stream_fetch_selects_url(plugin_module):
    """_get_all_streams must request 'url' or the flag can never work in production."""
    import inspect
    src = inspect.getsource(plugin_module.Plugin._get_all_streams)
    assert "'url'" in src or '"url"' in src
