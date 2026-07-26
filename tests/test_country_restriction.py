"""End-to-end tests for the country restriction filter (bug-158, bug-159)."""

import logging

LOGGER = logging.getLogger("test_country_restriction")


def test_resolve_restrict_coerces_string_false(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    assert inst._resolve_restrict_matching_to_country({"restrict_matching_to_country": "false"}) is False
    assert inst._resolve_restrict_matching_to_country({"restrict_matching_to_country": "true"}) is True
    assert inst._resolve_restrict_matching_to_country({"restrict_matching_to_country": True}) is True
    assert inst._resolve_restrict_matching_to_country({}) is False


def test_both_actions_load_before_reading_processed_data(plugin_module):
    """Spec §10: reading the flag from processed_data is safe only because both
    consuming actions call load_process_channels_action FIRST with live settings.
    That is an emergent property of two call sites, not an enforced invariant —
    this is the lock (bug-139 family)."""
    import inspect
    for action in ("preview_changes_action", "add_streams_to_channels_action"):
        src = inspect.getsource(getattr(plugin_module.Plugin, action))
        load_at = src.index("load_process_channels_action")
        # NB: the read spans a line break in the source —
        #     processed_data.get(\n    'restrict_matching_to_country', ...)
        # so match the quoted literal, not the whole call expression.
        read_at = src.index("'restrict_matching_to_country'")
        assert load_at < read_at, f"{action} reads the flag before loading channels"


def _inst(plugin_module):
    P = plugin_module.Plugin
    return P.__new__(P)


def test_channel_country_prefers_channel_database_entry(plugin_module):
    """bug-158: the channel DATABASE is the strongest signal. CNN/TNT/USA Network
    have no usable country marker in their own name or group, which is why the
    reporter's three rows all leaked."""
    inst = _inst(plugin_module)
    channel = {"name": "CNN", "channel_group__name": "News"}
    assert inst._channel_country_code(channel, {"channel_name": "CNN", "_country_code": "US"}) == "US"


def test_channel_country_falls_back_to_group_then_name(plugin_module):
    inst = _inst(plugin_module)
    assert inst._channel_country_code({"name": "CNN", "channel_group__name": "USA: News"}, None) == "US"
    assert inst._channel_country_code({"name": "UK: CNN", "channel_group__name": "News"}, None) == "UK"
    assert inst._channel_country_code({"name": "CNN", "channel_group__name": "News"}, None) is None


def test_channel_country_bare_group_us(plugin_module):
    """The reporter's probe row 2 — a group named exactly 'US'."""
    inst = _inst(plugin_module)
    assert inst._channel_country_code({"name": "CNN", "channel_group__name": "US"}, None) == "US"


def test_stream_country_group_first_then_name(plugin_module):
    inst = _inst(plugin_module)
    # This box's real group shape; every GO:/RK: stream lives in one of these.
    assert inst._stream_country_code(
        {"name": "GO: ESPN", "channel_group__name": "US| DIREC TV"}) == "US"
    assert inst._stream_country_code(
        {"name": "UK: CNN", "channel_group__name": "News"}) == "UK"
    # Unrecognized platform tag with no group signal -> UNKNOWN, must be kept.
    assert inst._stream_country_code({"name": "RK: DAZN RINGSIDE", "channel_group__name": None}) is None


def test_stream_country_reads_raw_name_not_match_name(plugin_module):
    """issue #36 consumer split: a regex rule that strips a country prefix for
    matching must not blind country detection."""
    inst = _inst(plugin_module)
    stream = {"name": "UK: CNN", "match_name": "CNN", "channel_group__name": None}
    assert inst._stream_country_code(stream) == "UK"


def test_channel_database_country_beats_conflicting_group(plugin_module):
    """Database-first must WIN over a conflicting group signal, not merely be a
    fallback when group/name resolve to nothing."""
    inst = _inst(plugin_module)
    channel = {"name": "CNN", "channel_group__name": "UK"}
    assert inst._channel_country_code(channel, {"channel_name": "CNN", "_country_code": "US"}) == "US"


import fuzzy_matcher


REPORTER_CNN_STREAMS = [
    {"id": 1, "name": "ARG: CNN"},
    {"id": 2, "name": "CNN USA"},
    {"id": 3, "name": "USA: CNN UHD"},
    {"id": 4, "name": "USA: CNN"},
    {"id": 5, "name": "UK: CNN"},
    {"id": 6, "name": "LBW: CNN"},
    {"id": 7, "name": "CA LBW: CNN"},
    {"id": 8, "name": "CA: CNN HD"},
    {"id": 9, "name": "CNN"},
    {"id": 10, "name": "PT: CNN"},
    {"id": 11, "name": "US CNN"},
    {"id": 12, "name": "MEX: CNN"},
]
for _s in REPORTER_CNN_STREAMS:
    _s.setdefault("channel_group__name", None)
    _s.setdefault("m3u_account", 1)
    _s.setdefault("url", "http://example/%d" % _s["id"])


def _matcher_inst(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_matcher.FuzzyMatcher()
    return inst


def _match(plugin_module, channel, streams, channels_data=None):
    inst = _matcher_inst(plugin_module)
    matched, _, _, _, _ = inst._match_streams_to_channel(
        channel, streams, LOGGER, channels_data=channels_data or [],
        restrict_matching_to_country=True)
    return [s["name"] for s in matched]


US_CNN_DB = [{"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "US"}]


def test_reporter_row_one_foreign_streams_excluded(plugin_module):
    """bug-158: the exact CNN row from the report. Channel group carries no
    country marker; the channel DATABASE supplies US."""
    got = _match(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                 REPORTER_CNN_STREAMS, US_CNN_DB)
    for foreign in ["ARG: CNN", "UK: CNN", "CA LBW: CNN", "CA: CNN HD", "PT: CNN", "MEX: CNN"]:
        assert foreign not in got, f"{foreign} should be excluded"


def test_reporter_row_one_domestic_and_unmarked_kept(plugin_module):
    got = _match(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                 REPORTER_CNN_STREAMS, US_CNN_DB)
    for keep in ["USA: CNN", "US CNN", "CNN"]:
        assert keep in got, f"{keep} should be kept"


def test_unrecognized_prefixes_are_kept_not_dropped(plugin_module):
    """bug-159 regression lock: GO:/RK:/LBW:/DR: are NOT countries. Treating an
    unrecognized country-shaped prefix as FOREIGN dropped 924/2439 real matches
    and zeroed 121 channels. This test is the guard against reintroducing it."""
    streams = [
        {"id": 1, "name": "GO: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u1"},
        {"id": 2, "name": "RK: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u2"},
        {"id": 3, "name": "LBW: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u3"},
        {"id": 4, "name": "DR: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u4"},
    ]
    got = _match(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                 streams, US_CNN_DB)
    assert sorted(got) == sorted([s["name"] for s in streams])


def test_fail_open_when_channel_country_unknown(plugin_module):
    """No database entry, no marker anywhere -> filter must not engage."""
    got = _match(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                 REPORTER_CNN_STREAMS, [])
    assert "UK: CNN" in got and "ARG: CNN" in got


def test_usa_network_uses_database_country(plugin_module):
    """Reporter row 3: the channel's own brand guard blocks name detection, so
    only the database entry can supply US."""
    streams = [
        {"id": 1, "name": "CA LBW: USA NETWORK", "channel_group__name": None, "m3u_account": 1, "url": "u1"},
        {"id": 2, "name": "USA: USA NETWORK", "channel_group__name": None, "m3u_account": 1, "url": "u2"},
    ]
    db = [{"channel_name": "USA Network", "type": "premium/cable/national", "_country_code": "US"}]
    got = _match(plugin_module, {"id": 1, "name": "USA Network", "channel_group__name": "Entertainment"},
                 streams, db)
    assert "CA LBW: USA NETWORK" not in got
    assert "USA: USA NETWORK" in got


def test_finalize_partitions_same_country_first(plugin_module):
    inst = _matcher_inst(plugin_module)
    a = {"id": 1, "name": "CNN", "m3u_account": 1, "url": "u1"}           # unknown
    b = {"id": 2, "name": "US: CNN", "m3u_account": 1, "url": "u2"}       # same
    out = inst._finalize_streams([a, b], False, same_country_ids={id(b)})
    assert [s["id"] for s in out] == [2, 1]


def test_finalize_partitions_before_dedup(plugin_module):
    """Dedup keys on (name, m3u_account) and keeps the FIRST occurrence, so the
    partition must run first or the confirmed domestic row can be discarded."""
    inst = _matcher_inst(plugin_module)
    unknown = {"id": 1, "name": "CNN", "m3u_account": 7, "url": "u1"}
    same = {"id": 2, "name": "CNN", "m3u_account": 7, "url": "u2"}
    out = inst._finalize_streams([unknown, same], False, same_country_ids={id(same)})
    assert [s["id"] for s in out] == [2]


def test_finalize_dispatches_through_self(plugin_module):
    """Six OTA tests stub _sort_streams_by_quality/_deduplicate_streams as instance
    attributes; _finalize_streams must call them via self or those stubs go dead."""
    inst = _matcher_inst(plugin_module)
    calls = []
    inst._sort_streams_by_quality = lambda s: (calls.append("sort"), s)[1]
    inst._deduplicate_streams = lambda s, allow_same_name_streams=False: (calls.append("dedup"), s)[1]
    inst._finalize_streams([{"id": 1, "name": "CNN", "m3u_account": 1, "url": "u"}], False)
    assert calls == ["sort", "dedup"]
