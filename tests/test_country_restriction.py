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


def test_country_tier_outranks_zone_affinity(plugin_module):
    """bug-158/bug-132: a DEFAULT-zone channel ranks generic streams at 0 and WEST
    at 2, so without this a proven same-country WEST feed loses order 0 to an
    unmarked generic one."""
    inst = _matcher_inst(plugin_module)
    unknown_generic = {"id": 1, "name": "Starz Encore", "m3u_account": 1, "url": "u1"}
    same_west = {"id": 2, "name": "US: Starz Encore (W)", "m3u_account": 1, "url": "u2"}
    out = inst._order_streams_for_zone(
        [same_west, unknown_generic], "DEFAULT", same_country_ids={id(same_west)})
    assert [s["id"] for s in out] == [2, 1]


def test_zone_order_unchanged_when_no_country_ids(plugin_module):
    inst = _matcher_inst(plugin_module)
    generic = {"id": 1, "name": "Starz Encore", "m3u_account": 1, "url": "u1"}
    west = {"id": 2, "name": "Starz Encore (W)", "m3u_account": 1, "url": "u2"}
    out = inst._order_streams_for_zone([west, generic], "DEFAULT", same_country_ids=None)
    assert [s["id"] for s in out] == [1, 2]


def test_assignment_path_preserves_country_order_over_zone(plugin_module):
    """Guards the inert-task failure mode: _order_streams_for_zone is only reached
    via _streams_for_channel, so the call sites must pass the country ids."""
    inst = _matcher_inst(plugin_module)
    generic = {"id": 1, "name": "Starz Encore", "m3u_account": 1, "url": "u1"}
    same_west = {"id": 2, "name": "US: Starz Encore (W)", "m3u_account": 1, "url": "u2"}
    ids = inst._same_country_ids_for(
        {"id": 9, "name": "Starz Encore", "channel_group__name": "USA: Movies"},
        [generic, same_west], [], LOGGER, True)
    out = inst._streams_for_channel([generic, same_west], 9, {9: "DEFAULT"}, ids)
    assert [s["id"] for s in out] == [2, 1]


def test_same_country_ids_for_returns_none_on_ambiguous_database_no_tiebreak(plugin_module):
    """bug-160/bug-158: _same_country_ids_for must resolve the channel country
    through the SAME ambiguity-aware path _match_streams_to_channel's filter
    uses, or the two can disagree. This is the real six-way CNN collision
    (channel_database=All matches BR/CA/ES/NL/UK/US) with no group/name
    tiebreak -- the filter fails open (keeps everything) for exactly this
    input, so the reorder helper must also decline to reorder, not fall
    through to a first-match (alphabetically BR) code that would promote
    Brazilian CNN streams to order 0 on a US channel."""
    inst = _matcher_inst(plugin_module)
    channel = {"id": 1, "name": "CNN", "channel_group__name": "News"}
    br_stream = {"id": 1, "name": "BR: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u1"}
    us_stream = {"id": 2, "name": "USA: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u2"}
    db = [
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "BR"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "CA"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "ES"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "NL"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "UK"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "US"},
    ]
    ids = inst._same_country_ids_for(channel, [br_stream, us_stream], db, LOGGER, True)
    assert ids is None

    # And therefore the assignment path leaves order untouched (zone-only, or
    # no-op if not zone-routed) rather than promoting the Brazilian stream.
    out = inst._streams_for_channel([br_stream, us_stream], 1, {1: "DEFAULT"}, ids)
    assert [s["id"] for s in out] == [1, 2]


def test_group_key_splits_by_country_when_enabled(plugin_module):
    """Matching runs once per GROUP on the leader channel and fans the result out,
    so a UK and a US channel sharing a normalized name would otherwise be governed
    by whichever sorted first."""
    inst = _matcher_inst(plugin_module)
    uk = {"id": 1, "name": "UK: CNN", "channel_group__name": None}
    us = {"id": 2, "name": "US CNN", "channel_group__name": None}
    assert inst._group_key_for_channel("cnn", uk, None, True) != \
           inst._group_key_for_channel("cnn", us, None, True)


def test_group_key_unchanged_when_disabled(plugin_module):
    inst = _matcher_inst(plugin_module)
    uk = {"id": 1, "name": "UK: CNN", "channel_group__name": None}
    us = {"id": 2, "name": "US CNN", "channel_group__name": None}
    assert inst._group_key_for_channel("cnn", uk, None, False) == \
           inst._group_key_for_channel("cnn", us, None, False) == "cnn"


def test_group_key_agrees_with_filter_via_channel_info_matches(plugin_module):
    """bug-158/bug-160: the group key must be resolved through the SAME
    ambiguity-aware path the filter uses (channel_info_matches), not the plain
    two-argument _channel_country_code form -- else a channel_database=All box
    could group a channel under a country the filter itself refuses to trust.

    `channel_info` is deliberately set to the naive single-match lookup's result
    (first-alphabetical BR, exactly what _get_channel_info_from_json would return
    for the real BR/CA/ES/NL/UK/US CNN collision) while `channel_info_matches`
    carries the full ambiguous set. This is the ONLY configuration where the two
    resolution paths diverge: the two-argument form reads channel_info directly
    and returns "BR" (key becomes "cnn@@BR"), while the correct three-argument,
    ambiguity-guarded form sees 2 disputed candidate codes, discards channel_info
    entirely, and falls through to the group/name signal -- None here, since
    "News" carries no country marker -- so the key stays unqualified. A test that
    passed channel_info=None would not discriminate: both paths already agree on
    None for a None channel_info, so it would pass under the very regression it
    claims to catch."""
    inst = _matcher_inst(plugin_module)
    channel = {"id": 1, "name": "CNN", "channel_group__name": "News"}
    naive_single_match = {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "BR"}
    ambiguous_matches = [
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "BR"},
        {"channel_name": "CNN", "type": "premium/cable/national", "_country_code": "US"},
    ]
    # Ambiguous database matches with no group/name tiebreak -> no usable code,
    # so the key must be left unqualified rather than split on a first-match guess.
    assert inst._group_key_for_channel(
        "cnn", channel, naive_single_match, True, ambiguous_matches) == "cnn"


# --- Task 7: observability counters -----------------------------------------


def test_match_streams_to_channel_return_arity_unchanged(plugin_module):
    """Guards the explicit constraint: adding country_stats must NOT change the
    5-tuple return shape several other tests unpack. Mutation this catches: a
    change to `return sorted_streams, ..., database_used, country_stats` (a
    smuggled 6th element) — the star-unpack below would raise ValueError."""
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
    result = inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=True, country_stats=stats)
    a, b, c, d, e = result  # noqa: F841 -- fails with wrong arity, that's the point


def test_country_stats_default_none_does_not_crash(plugin_module):
    """country_stats must default to None and every write site must be guarded
    -- omitting the parameter entirely (as every pre-Task-7 call site did, and
    as _get_matches_at_thresholds still does) must not raise."""
    inst = _matcher_inst(plugin_module)
    matched, *_ = inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=True)
    assert matched  # ran to completion with no stats dict supplied


def test_country_stats_counts_engaged_and_dropped(plugin_module):
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
    inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=True, country_stats=stats)
    assert stats["engaged"] == 1
    assert stats["skipped_unknown_channel"] == 0
    assert stats["foreign_dropped"] > 0


def test_country_stats_counts_skip_when_country_unknown(plugin_module):
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
    inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        REPORTER_CNN_STREAMS, LOGGER, channels_data=[],
        restrict_matching_to_country=True, country_stats=stats)
    assert stats["engaged"] == 0
    assert stats["skipped_unknown_channel"] == 1


def test_country_stats_untouched_when_restriction_disabled(plugin_module):
    """With the setting OFF, the filter block never runs, so a supplied
    country_stats dict must be left at its initial zeros -- proves the
    "no new output when off" constraint at the counter-bump layer."""
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
    inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=False, country_stats=stats)
    assert stats == {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}


def test_csv_header_reports_country_counts(plugin_module):
    inst = _matcher_inst(plugin_module)
    inst.version = "test"
    processed = {"restrict_matching_to_country": True,
                 "country_stats": {"engaged": 418, "skipped_unknown_channel": 412,
                                   "foreign_dropped": 1247, "unknown_kept": 388}}
    header = inst._generate_csv_header_comment({}, processed, action_name="Preview")
    assert "filter engaged on 418 channel group(s)" in header
    assert "skipped on 412" in header
    assert "1247" in header


def test_csv_header_omits_country_counts_when_disabled(plugin_module):
    """Mutation this catches: dropping the `if processed_data.get(
    'restrict_matching_to_country')` guard so the sub-lines print unconditionally
    -- setting off is the exact scenario that produced the original bug report
    (header claimed the filter was on/relevant when it silently did nothing)."""
    inst = _matcher_inst(plugin_module)
    inst.version = "test"
    header = inst._generate_csv_header_comment({}, {"restrict_matching_to_country": False},
                                               action_name="Preview")
    assert "filter engaged on" not in header


def test_csv_header_country_counts_default_to_zero_when_stats_missing(plugin_module):
    """restrict_matching_to_country True but no 'country_stats' key at all
    (e.g. an old processed_data.json written before this feature) must not
    raise and must render zeros, not crash the whole CSV header."""
    inst = _matcher_inst(plugin_module)
    inst.version = "test"
    header = inst._generate_csv_header_comment(
        {}, {"restrict_matching_to_country": True}, action_name="Preview")
    assert "filter engaged on 0 channel group(s)" in header
    assert "skipped on 0" in header


# --- follow-up fix: `details` dict must also be gated on the setting --------
#
# Review finding (post-Task-7): add_streams_to_channels_action's `details`
# dict added 'country_filter_skipped'/'country_foreign_dropped'
# UNCONDITIONALLY (even as zeros with the setting off), while the CSV,
# message suffix, and log line were all correctly gated. `details` is
# persisted to last_results.json (View Last Results renders it key-by-key)
# and forwarded verbatim to _fire_webhook, so a user who never enabled the
# setting would see two new rows/fields appear regardless. The plan's Global
# Constraints ("users with the setting OFF are byte-for-byte unaffected")
# outrank the task-7 brief's Step 3 snippet, which happened to show the keys
# added unconditionally. Fixed by extracting `_country_filter_details`,
# gated exactly like the CSV header sub-lines.

def test_country_filter_details_empty_when_disabled(plugin_module):
    """Setting off -> neither key may be present at all (not even as 0)."""
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
    result = inst._country_filter_details(False, stats)
    assert result == {}
    assert "country_filter_skipped" not in result
    assert "country_foreign_dropped" not in result


def test_country_filter_details_populated_when_enabled(plugin_module):
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 3, "skipped_unknown_channel": 2, "foreign_dropped": 7, "unknown_kept": 1}
    result = inst._country_filter_details(True, stats)
    assert result == {"country_filter_skipped": 2, "country_foreign_dropped": 7}


# --- Task 8: setting-off equivalence lock ------------------------------------
#
# "Users with the setting OFF are byte-for-byte unaffected" is the single claim
# the whole plan rests on. It was already contradicted once (Task 7's `details`
# dict leaked two zero-valued keys with the setting off, caught only by review
# and fixed above as `_country_filter_details`). Every surface Tasks 1-7 touched
# gets its own equivalence test here rather than trusting that "off" is merely
# the logical negation of the "on" tests already in this file.


def test_setting_off_matches_are_identical_to_unfiltered(plugin_module):
    """Global constraint: with the setting OFF nothing about matching changes.

    NB (adapted from the brief's literal snippet): id 2 ("CNN USA") does not
    clear the fuzzy matcher's own token-overlap threshold and is legitimately
    excluded for reasons unrelated to country detection -- the same
    pre-existing, non-country exclusion documented on
    test_fail_open_when_channel_country_unknown above. Asserting the full
    12-item REPORTER_CNN_STREAMS set survives would be a wrong (over-strict)
    expectation this codebase has never met, country restriction or not,
    so the expected set below is REPORTER_CNN_STREAMS minus id 2, matching
    what a country-restriction-free build already returns.

    Mutation this catches: the `if restrict_matching_to_country:` guard at the
    top of _match_streams_to_channel's country-filter block (plugin.py) being
    dropped, inverted, or replaced with a truthy-default -- any of which would
    start dropping REPORTER_CNN_STREAMS' foreign-looking rows (ARG:/UK:/CA:/
    PT:/MEX:) even with the setting off, shrinking `off` well below 11."""
    inst = _matcher_inst(plugin_module)
    channel = {"id": 1, "name": "CNN", "channel_group__name": "News"}
    off, _, _, _, _ = inst._match_streams_to_channel(
        channel, REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=False)
    expected_ids = {s["id"] for s in REPORTER_CNN_STREAMS} - {2}
    assert len(off) == len(expected_ids)
    assert {s["id"] for s in off} == expected_ids


def test_setting_off_group_key_has_no_country_suffix(plugin_module):
    """Mutation this catches: _group_key_for_channel resolving/appending a
    country suffix without checking its `restrict_to_country` parameter."""
    inst = _matcher_inst(plugin_module)
    key = inst._group_key_for_channel(
        "cnn", {"id": 1, "name": "UK: CNN", "channel_group__name": None}, None, False)
    assert "@@" not in key
    assert key == "cnn"


def test_setting_off_finalize_ordering_unchanged(plugin_module):
    """Mutation this catches: _finalize_streams reordering on a None
    same_country_ids (e.g. treating None as "no matches" -> reorder everything
    to the back, or crashing on `id(s) in None`) instead of passing straight
    through to sort+dedup untouched."""
    inst = _matcher_inst(plugin_module)
    streams = [{"id": i, "name": f"CNN {i}", "m3u_account": i, "url": f"u{i}"} for i in range(1, 5)]
    expected = inst._deduplicate_streams(inst._sort_streams_by_quality(list(streams)), False)
    got = inst._finalize_streams(list(streams), False, same_country_ids=None)
    assert [s["id"] for s in got] == [s["id"] for s in expected]


def test_setting_off_order_streams_for_zone_unchanged(plugin_module):
    """Ordering surface #2 named in the task: _order_streams_for_zone(...,
    same_country_ids=None) must reproduce pre-bug-158 zone-only ordering.
    Duplicate-in-spirit of test_zone_order_unchanged_when_no_country_ids
    above; kept as an explicit, separately-named lock for this task's audit
    trail since the brief calls this surface out by name."""
    inst = _matcher_inst(plugin_module)
    generic = {"id": 1, "name": "Starz Encore", "m3u_account": 1, "url": "u1"}
    west = {"id": 2, "name": "Starz Encore (W)", "m3u_account": 1, "url": "u2"}
    out = inst._order_streams_for_zone([west, generic], "DEFAULT", same_country_ids=None)
    assert [s["id"] for s in out] == [1, 2]


def test_setting_off_same_country_ids_for_returns_none(plugin_module):
    """_same_country_ids_for must short-circuit to None as soon as
    restrict_matching_to_country is False, without even looking at
    channels_data/streams -- so no reordering happens anywhere downstream.

    Mutation this catches: the `if not restrict_matching_to_country: return
    None` early-return at the top of _same_country_ids_for being removed,
    which would let a channel_database entry drive a reorder decision even
    with the setting off."""
    inst = _matcher_inst(plugin_module)
    channel = {"id": 1, "name": "CNN", "channel_group__name": "News"}
    us_stream = {"id": 1, "name": "US: CNN", "channel_group__name": None, "m3u_account": 1, "url": "u1"}
    ids = inst._same_country_ids_for(channel, [us_stream], US_CNN_DB, LOGGER, False)
    assert ids is None


def test_setting_off_csv_header_omits_country_sub_lines(plugin_module):
    """CSV surface: duplicate-in-spirit of
    test_csv_header_omits_country_counts_when_disabled above, restated here so
    this task's own coverage list is self-contained and independently
    greppable. Mutation this catches: dropping the
    `if processed_data.get('restrict_matching_to_country')` guard in
    _generate_csv_header_comment so the country sub-lines print unconditionally."""
    inst = _matcher_inst(plugin_module)
    inst.version = "test"
    header = inst._generate_csv_header_comment(
        {}, {"restrict_matching_to_country": False,
             "country_stats": {"engaged": 5, "skipped_unknown_channel": 5,
                                "foreign_dropped": 99, "unknown_kept": 0}},
        action_name="Preview")
    assert "filter engaged on" not in header
    assert "99" not in header


def test_setting_off_details_dict_has_neither_country_key(plugin_module):
    """The surface that actually broke once already (Task 7 review finding):
    duplicate-in-spirit of test_country_filter_details_empty_when_disabled,
    restated here with non-zero stats to prove the gate is on the `False` flag
    and not merely on the stats happening to be zero."""
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 4, "skipped_unknown_channel": 3, "foreign_dropped": 9, "unknown_kept": 1}
    result = inst._country_filter_details(False, stats)
    assert result == {}
    assert "country_filter_skipped" not in result
    assert "country_foreign_dropped" not in result


def test_setting_off_completion_message_suffix_never_triggers(plugin_module):
    """Completion-message surface (preview_changes_action /
    add_streams_to_channels_action): both actions append a country-filter
    warning to their success message only when
    `country_stats["skipped_unknown_channel"]` is truthy after accumulating
    across every channel group processed in the run. Simulates that
    accumulation loop -- several distinct channels sharing one stats dict,
    exactly how the real action loop calls _match_streams_to_channel once per
    group -- with the setting OFF, and proves the counter that gates the
    suffix stays at its initial zero.

    Mutation this catches: the `if restrict_matching_to_country:` guard
    around the country-filter block in _match_streams_to_channel being
    dropped/inverted, which would start incrementing
    skipped_unknown_channel with the setting off and silently print the
    warning suffix the user would see as "country filter skipped on N
    group(s)" despite never having enabled the feature.
    """
    inst = _matcher_inst(plugin_module)
    stats = {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
    channels = [
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        {"id": 2, "name": "USA Network", "channel_group__name": "Entertainment"},
        {"id": 3, "name": "Starz Encore", "channel_group__name": "Movies"},
    ]
    for channel in channels:
        inst._match_streams_to_channel(
            channel, REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
            restrict_matching_to_country=False, country_stats=stats)
    # This is exactly the condition both actions' message-building code reads
    # (plugin.py: `if country_stats["skipped_unknown_channel"]:`).
    assert stats["skipped_unknown_channel"] == 0
    assert stats == {"engaged": 0, "skipped_unknown_channel": 0, "foreign_dropped": 0, "unknown_kept": 0}
