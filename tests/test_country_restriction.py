"""End-to-end tests for the country restriction filter (bug-158, bug-159, bug-160)."""

import logging

import fuzzy_matcher

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


def test_channel_country_ambiguous_database_all_agree(plugin_module):
    """bug-160, single-database case still working: when channel_info_matches
    is supplied but every matching entry agrees on one _country_code (even
    with only one entry), that code is trusted exactly like the plain
    channel_info path."""
    inst = _inst(plugin_module)
    channel = {"name": "CNN", "channel_group__name": "News"}
    matches = [{"channel_name": "CNN", "_country_code": "US"}]
    assert inst._channel_country_code(channel, matches[0], matches) == "US"

    matches_agree = [
        {"channel_name": "CNN", "_country_code": "US"},
        {"channel_name": "CNN", "_country_code": "us"},  # case differs, still agrees
    ]
    assert inst._channel_country_code(channel, matches_agree[0], matches_agree) == "US"


def test_channel_country_ambiguous_database_disagree_group_tiebreak(plugin_module):
    """bug-160: channel_database=All can load CNN from BR/CA/ES/NL/UK/US
    simultaneously (verified against the shipped data). When those entries
    disagree, the database signal must never be trusted blindly -- here the
    group label happens to also say US, so the resolved code is still US, but
    via the group/name fallback, not because 'US' won a vote among the
    database candidates."""
    inst = _inst(plugin_module)
    channel = {"name": "CNN", "channel_group__name": "US"}
    matches = [
        {"channel_name": "CNN", "_country_code": "BR"},
        {"channel_name": "CNN", "_country_code": "US"},
        {"channel_name": "CNN", "_country_code": "UK"},
    ]
    assert inst._channel_country_code(channel, matches[0], matches) == "US"


def test_channel_country_ambiguous_database_disagree_no_tiebreak_falls_back(plugin_module):
    """bug-160: disagreeing database entries with NO group/name signal either
    must fail open (None), never guess one of the disputed candidates. This
    is the exact reporter shape: channel_database=All, CNN's own name/group
    carry no country marker, and the six-way CNN collision must not pick BR
    (the alphabetically-first shipped database) or any other candidate."""
    inst = _inst(plugin_module)
    channel = {"name": "CNN", "channel_group__name": "News"}
    matches = [
        {"channel_name": "CNN", "_country_code": "BR"},
        {"channel_name": "CNN", "_country_code": "CA"},
        {"channel_name": "CNN", "_country_code": "ES"},
        {"channel_name": "CNN", "_country_code": "NL"},
        {"channel_name": "CNN", "_country_code": "UK"},
        {"channel_name": "CNN", "_country_code": "US"},
    ]
    assert inst._channel_country_code(channel, matches[0], matches) is None


def test_all_agree_end_to_end_with_channels_data(plugin_module):
    """End-to-end through _match_streams_to_channel: channels_data carries the
    SAME channel_name from two databases that happen to agree on the code
    (e.g. loaded twice, or two entries for the same real country) -- the
    unambiguous case must still filter normally."""
    streams = [
        {"id": 1, "name": "US: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u1"},
        {"id": 2, "name": "UK: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u2"},
    ]
    db = [
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "US"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "US"},
    ]
    got = _match(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                 streams, db)
    assert "UK: CNN" not in got
    assert "US: CNN" in got


def test_ambiguous_database_all_end_to_end_fails_open(plugin_module):
    """bug-160 end-to-end: this is exactly what channel_database=All produces
    for CNN. Without the unambiguous-only guard, the database's first
    (alphabetically) match BR would classify 'US: CNN' as FOREIGN and drop it.
    With the guard, disagreement with no group/name tiebreak fails open and
    nothing is removed."""
    streams = [
        {"id": 1, "name": "US: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u1"},
        {"id": 2, "name": "UK: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u2"},
    ]
    db = [
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "BR"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "CA"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "ES"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "NL"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "UK"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "US"},
    ]
    got = _match(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                 streams, db)
    assert "US: CNN" in got
    assert "UK: CNN" in got


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
    _s.setdefault("url", f"http://example/{_s['id']}")


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
    """No database entry, no marker anywhere -> filter must not engage.

    Compares against a restrict_matching_to_country=False baseline rather
    than a hardcoded stream count: one of the twelve ("CNN USA") does not
    clear the fuzzy matcher's own token-overlap threshold and is legitimately
    excluded for reasons unrelated to country detection, so len(got) == 12
    would be a wrong (over-strict) assertion. Comparing to the real baseline
    is still a genuine control for all six exclusion assertions above: if the
    filter wrongly engaged here, the count would drop below baseline.
    """
    channel = {"id": 1, "name": "CNN", "channel_group__name": "News"}
    inst = _matcher_inst(plugin_module)
    baseline, _, _, _, _ = inst._match_streams_to_channel(
        channel, REPORTER_CNN_STREAMS, LOGGER, channels_data=[],
        restrict_matching_to_country=False)
    got = _match(plugin_module, channel, REPORTER_CNN_STREAMS, [])
    assert len(got) == len(baseline)
    assert "UK: CNN" in got and "ARG: CNN" in got


def test_country_filter_never_reorders_working_streams(plugin_module):
    """The filter must only REMOVE, never REORDER, working_streams: that list
    feeds fuzzy_matcher.fuzzy_match(), whose single winning name gates the
    whole result set, so a same-country-first reorder here (instead of only
    in _finalize_streams, after matching is done) would silently change which
    stream wins. Pins this by stubbing fuzzy_match and asserting the
    stream_names it receives are in ORIGINAL input order, with the confirmed
    same-country stream deliberately placed LAST in the input.
    """
    inst = _matcher_inst(plugin_module)
    calls = []

    def fake_fuzzy_match(channel_name, stream_names, *args, **kwargs):
        calls.append(list(stream_names))
        return None, 0, None

    inst.fuzzy_matcher.fuzzy_match = fake_fuzzy_match

    streams = [
        # unknown country (no group, no name marker) -> kept, FIRST in input
        {"id": 1, "name": "CNN Extra", "channel_group__name": None, "m3u_account": 1, "url": "u1"},
        # proven-foreign -> removed entirely
        {"id": 2, "name": "UK: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u2"},
        # confirmed same-country -> kept, LAST in input
        {"id": 3, "name": "US: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u3"},
    ]
    inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        streams, LOGGER, channels_data=[{"channel_name": "CNN", "_country_code": "US"}],
        restrict_matching_to_country=True)

    assert calls, "fuzzy_match was never called"
    # UK: CNN (FOREIGN) is removed, but the two survivors keep their ORIGINAL
    # relative order -- the confirmed same-country stream stays LAST, not
    # promoted to the front.
    assert calls[0] == ["CNN Extra", "US: CNN"]


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


def test_finalize_no_reorder_when_filter_did_not_engage(plugin_module):
    """Control for the test above: same_country_ids=None (filter never engaged)
    must leave input order untouched, byte-identical to pre-bug-158 behaviour."""
    inst = _matcher_inst(plugin_module)
    a = {"id": 1, "name": "CNN", "m3u_account": 1, "url": "u1"}
    b = {"id": 2, "name": "US: CNN", "m3u_account": 1, "url": "u2"}
    out = inst._finalize_streams([a, b], False, same_country_ids=None)
    assert [s["id"] for s in out] == [1, 2]


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


# --- _get_matches_at_thresholds: IMPORTANT-3 (untested twin of the filter
# in _match_streams_to_channel; a wrong variable -- all_streams instead of
# candidate_streams -- or a dropped same_country_ids argument would ship
# green without these) ---

def _thresholds(plugin_module, channel, streams, channels_data=None, current_threshold=85):
    inst = _matcher_inst(plugin_module)
    results = inst._get_matches_at_thresholds(
        channel, streams, LOGGER,
        ignore_tags=[], ignore_quality=True, ignore_regional=True,
        ignore_geographic=True, ignore_misc=True,
        channels_data=channels_data or [], current_threshold=current_threshold,
        restrict_matching_to_country=True)
    names = set()
    for entry in results.values():
        names.update(s["name"] for s in entry["streams"])
    return names


def test_get_matches_at_thresholds_excludes_foreign(plugin_module):
    """Direct coverage of _get_matches_at_thresholds' own country filter --
    previously only _match_streams_to_channel was exercised, so a wrong
    variable (all_streams instead of candidate_streams) or a dropped
    same_country_ids argument here would have shipped green."""
    got = _thresholds(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                       REPORTER_CNN_STREAMS, US_CNN_DB)
    for foreign in ["ARG: CNN", "UK: CNN", "CA LBW: CNN", "CA: CNN HD", "PT: CNN", "MEX: CNN"]:
        assert foreign not in got, f"{foreign} should be excluded"
    for keep in ["USA: CNN", "US CNN", "CNN"]:
        assert keep in got, f"{keep} should be kept"


def test_get_matches_at_thresholds_keeps_unknown_when_country_unresolved(plugin_module):
    """No database entry, no marker anywhere -> the filter in
    _get_matches_at_thresholds must not engage either."""
    got = _thresholds(plugin_module, {"id": 1, "name": "CNN", "channel_group__name": "News"},
                       REPORTER_CNN_STREAMS, [])
    assert "UK: CNN" in got and "ARG: CNN" in got


def test_get_matches_at_thresholds_orders_same_country_first(plugin_module):
    """Confirms same_country_ids is actually THREADED from this function's own
    filter through to _finalize_streams, not just computed and discarded --
    a dropped argument at either of this function's two _finalize_streams
    call sites would pass the exclusion tests above but fail this one."""
    inst = _matcher_inst(plugin_module)
    streams = [
        {"id": 1, "name": "CNN", "channel_group__name": None, "m3u_account": 1, "url": "u1"},      # unknown
        {"id": 2, "name": "US: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u2"},   # same
    ]
    results = inst._get_matches_at_thresholds(
        {"id": 1, "name": "CNN", "channel_group__name": "News"}, streams, LOGGER,
        ignore_tags=[], ignore_quality=True, ignore_regional=True,
        ignore_geographic=True, ignore_misc=True,
        channels_data=US_CNN_DB, current_threshold=85,
        restrict_matching_to_country=True)
    assert results, "expected at least one threshold to match"
    for threshold, entry in results.items():
        ids = [s["id"] for s in entry["streams"]]
        assert ids[0] == 2, f"same-country stream should sort first at {threshold}: {ids}"
