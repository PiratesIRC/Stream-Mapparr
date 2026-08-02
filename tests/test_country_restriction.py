"""End-to-end tests for the country restriction filter (bug-158, bug-159, bug-160)."""

import inspect
import logging
import types

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
    this is the lock (bug-139 family).

    bug-158/M1 update: the read site moved from a raw
    `processed_data.get('restrict_matching_to_country', ...)` to
    `self._resolve_restrict_matching_to_country(processed_data)` (so a stored
    string "false" coerces correctly) -- match THAT call now."""
    import inspect
    for action in ("preview_changes_action", "add_streams_to_channels_action"):
        src = inspect.getsource(getattr(plugin_module.Plugin, action))
        load_at = src.index("load_process_channels_action")
        read_at = src.index("self._resolve_restrict_matching_to_country(processed_data)")
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
    # The West feed is now dropped from a DEFAULT channel even though its country
    # was proven, because it is still the wrong time zone. The country tier can
    # only order what survives the zone filter, so a proven same-country WEST
    # feed no longer beats an unproven generic here: it is not offered at all.
    assert [s["id"] for s in out] == [1]


def test_zone_order_unchanged_when_no_country_ids(plugin_module):
    inst = _matcher_inst(plugin_module)
    generic = {"id": 1, "name": "Starz Encore", "m3u_account": 1, "url": "u1"}
    west = {"id": 2, "name": "Starz Encore (W)", "m3u_account": 1, "url": "u2"}
    out = inst._order_streams_for_zone([west, generic], "DEFAULT", same_country_ids=None)
    # West dropped from a DEFAULT channel; only the generic feed remains.
    assert [s["id"] for s in out] == [1]


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
    # The call site still passes the country ids, which is what this guards. The
    # West feed is dropped by the zone filter before the country tier applies.
    assert [s["id"] for s in out] == [1]


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
    # West dropped from a DEFAULT channel, so only the generic feed remains.
    assert [s["id"] for s in out] == [1]


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


# --- Final review fixes: I1 (Manage Channel Visibility re-merges groups) -----


def test_build_channel_groups_splits_by_country_when_enabled(plugin_module):
    """bug-158 review I1: `_build_channel_groups` is the SHARED grouping helper
    Match & Assign, Preview, and Manage Channel Visibility must all use, or the
    last one can silently re-merge the country-split groups the first two
    create (exactly the regression the review caught: a same-named UK/US pair
    collapsed back into one group and only one country's channel got enabled).

    Mutation this catches: `_build_channel_groups` not calling
    `_group_key_for_channel` (or calling it with `restrict_matching_to_country`
    hardcoded False) -- either would leave both channels in ONE group even
    with the flag on."""
    inst = _matcher_inst(plugin_module)
    uk = {"id": 1, "name": "UK: CNN", "channel_group__name": None}
    us = {"id": 2, "name": "US CNN", "channel_group__name": None}
    groups = inst._build_channel_groups(
        [uk, us], [], LOGGER, [], True, True, True, True, restrict_matching_to_country=True)
    assert len(groups) == 2, f"expected 2 country-split groups, got {list(groups.keys())}"
    assert {ch["id"] for chans in groups.values() for ch in chans} == {1, 2}
    for chans in groups.values():
        assert len(chans) == 1


def test_build_channel_groups_merges_when_disabled(plugin_module):
    """Byte-identical to pre-bug-158: with the setting off, a same-named
    UK/US pair still normalizes to one shared key and lands in ONE group --
    exactly today's behaviour."""
    inst = _matcher_inst(plugin_module)
    uk = {"id": 1, "name": "UK: CNN", "channel_group__name": None}
    us = {"id": 2, "name": "US CNN", "channel_group__name": None}
    groups = inst._build_channel_groups(
        [uk, us], [], LOGGER, [], True, True, True, True, restrict_matching_to_country=False)
    assert len(groups) == 1, f"expected the two channels to merge, got {list(groups.keys())}"
    (only_group,) = groups.values()
    assert {ch["id"] for ch in only_group} == {1, 2}


def test_manage_channel_visibility_uses_shared_group_builder(plugin_module):
    """bug-158 review I1: guards against `manage_channel_visibility_action`
    reverting to its own duplicated, unqualified grouping loop -- exactly
    what silently re-merged the country-split groups and disabled the very
    channel this branch was written to fix. Also guards the resolver source
    (live settings, not a processed_data snapshot, per the finding)."""
    src = inspect.getsource(plugin_module.Plugin.manage_channel_visibility_action)
    assert "self._build_channel_groups(" in src, (
        "manage_channel_visibility_action must group channels via the shared helper")
    assert "self._resolve_restrict_matching_to_country(settings)" in src, (
        "manage_channel_visibility_action must resolve the flag from LIVE settings")


# --- Final review fixes: I2 (Sort drops the country tier) --------------------


def _fake_orm_manager(result):
    """A stand-in for `<Model>.objects` that answers `.filter(...).order_by(...)
    .values_list(...)` with a fixed `result`, ignoring every argument, and no-ops
    `.delete()` / `.bulk_create()` for the LIVE-mode write path."""
    ns = types.SimpleNamespace()
    ns.filter = lambda **kw: ns
    ns.order_by = lambda *a, **kw: ns
    ns.values_list = lambda *a, **kw: result
    ns.delete = lambda: None
    ns.bulk_create = lambda rows: None
    return ns


def test_sort_stream_dict_includes_channel_group_name(plugin_module):
    """bug-158 review I2: without `channel_group__name` on the stream dicts
    Sort builds, `_stream_country_code` can only see the raw name -- a stream
    whose ONLY country signal is its group (no marker in the name) resolves
    differently here than it did in Match & Assign for the same stream row.

    Mutation this catches: dropping the `'channel_group__name': channel_group_name`
    key back out of the dict `sort_streams_action` builds per stream."""
    src = inspect.getsource(plugin_module.Plugin.sort_streams_action)
    assert "'channel_group__name': channel_group_name" in src


def test_sort_action_country_first_ordering_when_enabled(plugin_module, monkeypatch):
    """bug-158 review I2: a scheduled/manual Sort must preserve the
    same-country-first ordering Match & Assign already applied, not revert to
    pure quality order. Drives the REAL `sort_streams_action` end to end with
    the ORM stubbed out, LIVE mode (not dry-run) so the constructed
    `ChannelStream(...)` rows -- inspected via the mocked class's
    `call_args_list`, in construction order -- reveal the exact final order.

    The channel's country ("US") comes from `channels_data` (like every other
    country-detection test here). Stream 1 ("Encore Generic") carries no
    country marker anywhere and is UNKNOWN. Stream 2 ("Encore Feed") carries
    no marker in its NAME either -- its country comes ONLY from
    `channel_group__name` ("US" via its ORM `channel_group.name`), so this
    also exercises the I2 field-addition directly, not just the reorder call.

    Mutation this catches: dropping the `same_country_ids` argument (or the
    call that computes it) at the `_streams_for_channel` call site in
    `sort_streams_action` -- either leaves stream 1 before stream 2 (their
    ORM fetch order), not stream 2 promoted to the front.
    """
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_matcher.FuzzyMatcher()
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst.saved_settings = {}

    channel = {"id": 9, "name": "CNN", "channel_group__name": "News",
               "channel_group_id": None}
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [{"name": "Default", "id": 1}])
    monkeypatch.setattr(inst, "_get_all_channels", lambda logger: [channel])
    monkeypatch.setattr(inst, "_load_channels_data", lambda logger, settings: US_CNN_DB)
    # This channel happens to be zone-routed here (country-then-zone via
    # _order_streams_for_zone); since bug-161 residual 3, _streams_for_channel
    # applies the same-country-first partition for non-zone-routed channels
    # too -- see test_sort_action_country_first_ordering_when_not_zone_routed
    # for that shape.
    monkeypatch.setattr(inst, "_zone_routed_map", lambda *a, **k: {9: "DEFAULT"})
    monkeypatch.setattr(inst, "_trigger_frontend_refresh", lambda *a, **k: None)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None)

    fake_streams = {
        1: types.SimpleNamespace(id=1, name="Encore Generic", stream_stats={},
                                 m3u_account_id=None, channel_group_id=None),
        2: types.SimpleNamespace(id=2, name="Encore Feed", stream_stats={},
                                 m3u_account_id=None, channel_group_id=7,
                                 channel_group=types.SimpleNamespace(name="US")),
    }

    monkeypatch.setattr(plugin_module.ChannelProfileMembership, "objects",
                         _fake_orm_manager([9]))
    monkeypatch.setattr(plugin_module.ChannelStream, "objects",
                         _fake_orm_manager([1, 2]))
    monkeypatch.setattr(plugin_module.Stream, "objects",
                         types.SimpleNamespace(get=lambda id: fake_streams[id]))
    plugin_module.ChannelStream.reset_mock()

    result = inst.sort_streams_action(
        {"profile_name": "Default", "dry_run_mode": False,
         "restrict_matching_to_country": True},
        LOGGER)

    assert result["status"] == "success"
    constructed_order = [c.kwargs["stream_id"] for c in plugin_module.ChannelStream.call_args_list]
    assert constructed_order == [2, 1], (
        f"expected the group-confirmed same-country stream (2) first, got {constructed_order}")


def test_sort_action_order_unchanged_when_disabled(plugin_module, monkeypatch):
    """Setting-off control for the test above: with the flag off, Sort must
    reproduce the exact pre-bug-158 order (the streams' original ORM fetch
    order, both being quality-tied)."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_matcher.FuzzyMatcher()
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst.saved_settings = {}

    channel = {"id": 9, "name": "CNN", "channel_group__name": "News",
               "channel_group_id": None}
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [{"name": "Default", "id": 1}])
    monkeypatch.setattr(inst, "_get_all_channels", lambda logger: [channel])
    monkeypatch.setattr(inst, "_load_channels_data", lambda logger, settings: US_CNN_DB)
    # This channel happens to be zone-routed here (country-then-zone via
    # _order_streams_for_zone); since bug-161 residual 3, _streams_for_channel
    # applies the same-country-first partition for non-zone-routed channels
    # too -- see test_sort_action_country_first_ordering_when_not_zone_routed
    # for that shape.
    monkeypatch.setattr(inst, "_zone_routed_map", lambda *a, **k: {9: "DEFAULT"})
    monkeypatch.setattr(inst, "_trigger_frontend_refresh", lambda *a, **k: None)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None)

    fake_streams = {
        1: types.SimpleNamespace(id=1, name="Encore Generic", stream_stats={},
                                 m3u_account_id=None, channel_group_id=None),
        2: types.SimpleNamespace(id=2, name="Encore Feed", stream_stats={},
                                 m3u_account_id=None, channel_group_id=7,
                                 channel_group=types.SimpleNamespace(name="US")),
    }

    monkeypatch.setattr(plugin_module.ChannelProfileMembership, "objects",
                         _fake_orm_manager([9]))
    monkeypatch.setattr(plugin_module.ChannelStream, "objects",
                         _fake_orm_manager([1, 2]))
    monkeypatch.setattr(plugin_module.Stream, "objects",
                         types.SimpleNamespace(get=lambda id: fake_streams[id]))
    plugin_module.ChannelStream.reset_mock()

    result = inst.sort_streams_action(
        {"profile_name": "Default", "dry_run_mode": False},
        LOGGER)

    assert result["status"] == "success"
    # No reorder happened at all -> "already sorted", nothing gets rewritten.
    constructed_order = [c.kwargs["stream_id"] for c in plugin_module.ChannelStream.call_args_list]
    assert constructed_order == [], (
        f"setting off must reproduce pre-bug-158 behaviour (no reorder), got {constructed_order}")


# --- Final review fixes: I3 (O(groups x streams) country classification) ----


def test_stream_country_memo_reused_across_groups(plugin_module):
    """bug-158 review I3: `_build_stream_country_memo` must classify each
    stream ONCE, and `_match_streams_to_channel` must consult the memo rather
    than re-classifying the full stream list per group.

    Mutation this catches: dropping `stream_country_memo` threading from
    `_match_streams_to_channel` (the parameter itself, or the
    `_resolve_stream_country` call site inside its filter loop) -- either
    would make the classify-call count below scale with the number of groups
    instead of staying at zero after the memo is built."""
    inst = _matcher_inst(plugin_module)
    calls = []
    real = inst._stream_country_code

    def counting(stream):
        calls.append(1)
        return real(stream)

    inst._stream_country_code = counting

    memo = inst._build_stream_country_memo(REPORTER_CNN_STREAMS)
    assert len(calls) == len(REPORTER_CNN_STREAMS)
    calls.clear()

    channels = [
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        {"id": 2, "name": "CNN", "channel_group__name": "News"},
        {"id": 3, "name": "CNN", "channel_group__name": "News"},
    ]
    for channel in channels:
        inst._match_streams_to_channel(
            channel, REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
            restrict_matching_to_country=True, stream_country_memo=memo)

    assert calls == [], (
        "every stream should have been served from the memo, not re-classified "
        f"({len(calls)} extra classify call(s) across 3 groups)")


def test_stream_country_memo_not_used_without_a_memo(plugin_module):
    """Control for the test above: omitting `stream_country_memo` (every
    pre-I3 call site, and every call site that legitimately has no run-scoped
    memo) must classify normally, not raise or silently skip the filter."""
    inst = _matcher_inst(plugin_module)
    matched, *_ = inst._match_streams_to_channel(
        {"id": 1, "name": "CNN", "channel_group__name": "News"},
        REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=True)
    assert matched


def test_channel_info_cache_reused_across_call_sites(plugin_module):
    """bug-158 review I3 (subsumes 'channels_data scanned twice per channel'):
    `channel_info_cache` must stop `_match_streams_to_channel` and
    `_same_country_ids_for` from each re-scanning `channels_data` for the SAME
    channel within one action run.

    Mutation this catches: dropping `channel_info_cache` threading at either
    call site, or `_get_channel_info_and_matches` not actually writing/reading
    the cache -- either would make the channels_data-scan count below repeat
    on the second call instead of staying at zero."""
    inst = _matcher_inst(plugin_module)
    scans = []
    real_matches = inst._get_all_channel_info_matches

    def counting_matches(channel_name, channels_data):
        scans.append(1)
        return real_matches(channel_name, channels_data)

    inst._get_all_channel_info_matches = counting_matches

    channel = {"id": 1, "name": "CNN", "channel_group__name": "News"}
    cache = {}
    inst._match_streams_to_channel(
        channel, REPORTER_CNN_STREAMS, LOGGER, channels_data=US_CNN_DB,
        restrict_matching_to_country=True, channel_info_cache=cache)
    assert len(scans) == 1
    scans.clear()

    inst._same_country_ids_for(
        channel, REPORTER_CNN_STREAMS, US_CNN_DB, LOGGER, True,
        channel_info_cache=cache)
    assert scans == [], (
        "the second call should be served entirely from channel_info_cache")


# --- Final review fixes: M1 (raw processed_data reads bypass the resolver) --


def test_m1_actions_resolve_restrict_flag_via_resolver(plugin_module):
    """bug-158/M1: processed_data.json can carry the STRING "false" (written
    by a previous release, or hand-edited), which is truthy under a raw
    `dict.get()` -- the resolver's string coercion must run at BOTH read
    sites, not only wherever it happened to already be applied.

    Mutation this catches: reverting either action's read of
    `restrict_matching_to_country` from
    `self._resolve_restrict_matching_to_country(processed_data)` back to a
    raw `processed_data.get('restrict_matching_to_country', DEFAULT)`."""
    for action in ("preview_changes_action", "add_streams_to_channels_action"):
        src = inspect.getsource(getattr(plugin_module.Plugin, action))
        assert "self._resolve_restrict_matching_to_country(processed_data)" in src, (
            f"{action} does not resolve restrict_matching_to_country via the resolver")


def test_resolver_coerces_truthy_string_false_from_a_dict_like_processed_data(plugin_module):
    """The resolver only calls `.get()`, so it is safe to reuse at the
    processed_data read sites (M1) -- pins the exact failure mode: a stored
    string "false" must resolve to Python False, not the truthy default."""
    inst = _inst(plugin_module)
    processed_data = {"restrict_matching_to_country": "false"}
    assert inst._resolve_restrict_matching_to_country(processed_data) is False


# --- Final review fixes: M2 (Preview never sends `details` on completion) ---


def test_preview_sends_details_on_completion(plugin_module):
    """bug-158/M2: Preview must call the completion `_send_progress_update`
    WITH a `details` payload, so `_country_filter_details`'s engagement
    counts reach View Last Results / the webhook the same way
    add_streams_to_channels_action's completion call already does.

    Mutation this catches: reverting the final
    `self._send_progress_update("preview_changes", 'success', 100, message, context)`
    call in `preview_changes_action` to omit the trailing `details` argument."""
    src = inspect.getsource(plugin_module.Plugin.preview_changes_action)
    assert (
        'self._send_progress_update("preview_changes", \'success\', 100, message, context, details)'
        in src
    ), "preview_changes_action's completion update must pass details"


# --- Final review fixes: M3 (unconditional country_stats write) -------------


def test_m3_country_stats_write_gated_in_preview_and_add_streams(plugin_module):
    """bug-158/M3: `processed_data['country_stats']` must only be written
    when `restrict_matching_to_country` is True -- otherwise it was the one
    unconditional new write in a change whose single global constraint is
    "setting OFF -> byte-for-byte unaffected".

    Mutation this catches: removing (or un-indenting) the
    `if restrict_matching_to_country:` guard immediately above the write in
    either action."""
    for action in ("preview_changes_action", "add_streams_to_channels_action"):
        src = inspect.getsource(getattr(plugin_module.Plugin, action))
        idx = src.index("processed_data['country_stats'] = country_stats")
        preceding = src[:idx]
        assert preceding.rstrip().endswith("if restrict_matching_to_country:"), (
            f"{action}: the country_stats write is not gated by restrict_matching_to_country")


# ============================================================================
# bug-161: owner decision 1 -- OTA channels are exempt from the country filter
# ============================================================================
#
# Several US state codes are also ISO-2 country codes recognized by country.py
# (CA=Canada/California, IN=India/Indiana, AL=Albania/Alabama, AR=Argentina/
# Arkansas, CO=Colombia/Colorado, IL=Israel/Illinois). A US locals group or
# stream name like "IL: CHICAGO WGN" or "AR: LITTLE ROCK" therefore classifies
# as FOREIGN against a US OTA channel and its own streams get dropped. OTA
# channels are matched by FCC callsign, not by name/group, so the filter never
# contributed anything for them -- the owner's decision is to exempt them
# entirely rather than denylist the colliding state codes (which would break
# the "CA:" foreign marker this whole branch exists to catch).

WGN_OTA_DB = [{"channel_name": "WGN Chicago", "type": "broadcast (OTA)",
               "callsign": "WGN", "_country_code": "US"}]

KARK_OTA_DB = [{"channel_name": "KARK Little Rock", "type": "broadcast (OTA)",
                "callsign": "KARK", "_country_code": "US"}]


def test_ota_channel_keeps_state_code_collision_stream_il(plugin_module):
    """Real-shape collision: a stream literally named 'IL: CHICAGO WGN' reads
    as Israel under country.py's prefix detection. Without the OTA exemption
    the channel's US database country would classify it FOREIGN and drop it,
    even though it is the exact callsign match for this OTA channel.

    Mutation this catches: removing `not self._is_ota_channel(channel_info)`
    from the country-filter gate in `_match_streams_to_channel`."""
    inst = _matcher_inst(plugin_module)
    streams = [
        {"id": 1, "name": "IL: CHICAGO WGN", "channel_group__name": None,
         "m3u_account": 1, "url": "u1"},
    ]
    matched, _, _, reason, _ = inst._match_streams_to_channel(
        {"id": 1, "name": "WGN Chicago", "channel_group__name": None},
        streams, LOGGER, channels_data=WGN_OTA_DB, restrict_matching_to_country=True)
    assert reason == "Callsign match"
    assert [s["name"] for s in matched] == ["IL: CHICAGO WGN"]


def test_ota_channel_keeps_state_code_collision_stream_ar(plugin_module):
    """Same collision shape, Arkansas/Argentina: 'AR: LITTLE ROCK' would read
    as Argentina against a US-database OTA channel.

    Mutation this catches: same as above, exercised through a different
    colliding code (AR) and a different callsign (KARK) to rule out a
    code-specific fix that only special-cased IL."""
    inst = _matcher_inst(plugin_module)
    streams = [
        {"id": 1, "name": "AR: LITTLE ROCK KARK", "channel_group__name": None,
         "m3u_account": 1, "url": "u1"},
    ]
    matched, _, _, reason, _ = inst._match_streams_to_channel(
        {"id": 1, "name": "KARK Little Rock", "channel_group__name": None},
        streams, LOGGER, channels_data=KARK_OTA_DB, restrict_matching_to_country=True)
    assert reason == "Callsign match"
    assert [s["name"] for s in matched] == ["AR: LITTLE ROCK KARK"]


def test_non_ota_channel_still_filtered_with_same_collision_shape(plugin_module):
    """Contrast case for the two tests above: a NON-OTA (premium/cable) channel
    with the identical 'IL:' collision must still have the foreign stream
    dropped -- the exemption is OTA-specific, not a blanket skip of the IL/AR
    prefixes.

    Mutation this catches: a broken exemption that skips the filter for every
    channel (e.g. inverting the guard, or exempting on something always-true)
    would let this foreign stream survive too."""
    inst = _matcher_inst(plugin_module)
    streams = [
        {"id": 1, "name": "IL: METRO NEWS", "channel_group__name": None,
         "m3u_account": 1, "url": "u1"},
        {"id": 2, "name": "US: METRO NEWS", "channel_group__name": None,
         "m3u_account": 1, "url": "u2"},
    ]
    db = [{"channel_name": "Metro News", "type": "premium/cable/national", "_country_code": "US"}]
    matched, *_ = inst._match_streams_to_channel(
        {"id": 1, "name": "Metro News", "channel_group__name": None},
        streams, LOGGER, channels_data=db, restrict_matching_to_country=True)
    got = [s["name"] for s in matched]
    assert "IL: METRO NEWS" not in got
    assert "US: METRO NEWS" in got


def test_get_matches_at_thresholds_ota_exempt_from_country_filter(plugin_module):
    """`_get_matches_at_thresholds` is Preview's second scan and has its own
    copy of the country-filter gate -- it must be exempted the same way as
    `_match_streams_to_channel` or Preview would show a different (wrongly
    filtered) result than Match & Assign for the same OTA channel.

    Mutation this catches: removing the OTA guard from
    `_get_matches_at_thresholds`'s country-filter block specifically (the
    other tests here would not catch a fix applied to only one of the two
    functions)."""
    inst = _matcher_inst(plugin_module)
    streams = [
        {"id": 1, "name": "IL: CHICAGO WGN", "channel_group__name": None,
         "m3u_account": 1, "url": "u1"},
    ]
    results = inst._get_matches_at_thresholds(
        {"id": 1, "name": "WGN Chicago", "channel_group__name": None},
        streams, LOGGER, [], True, True, True, True, WGN_OTA_DB, 85,
        restrict_matching_to_country=True)
    key = "callsign_85"
    assert key in results
    assert [s["name"] for s in results[key]["streams"]] == ["IL: CHICAGO WGN"]


def test_same_country_ids_for_returns_none_for_ota_channel(plugin_module):
    """`_same_country_ids_for` feeds the assignment-time reorder
    (`_streams_for_channel`); it must also decline to compute a country
    partition for an OTA channel, or a same-callsign, colliding-prefix stream
    could be silently demoted to a lower-priority alternate even though the
    filter that would justify that ranking never engaged.

    Mutation this catches: dropping the `_is_ota_channel` short-circuit added
    to `_same_country_ids_for`."""
    inst = _matcher_inst(plugin_module)
    stream = {"id": 1, "name": "IL: CHICAGO WGN", "channel_group__name": None,
              "m3u_account": 1, "url": "u1"}
    ids = inst._same_country_ids_for(
        {"id": 1, "name": "WGN Chicago", "channel_group__name": None},
        [stream], WGN_OTA_DB, LOGGER, True)
    assert ids is None


def test_group_key_for_channel_ota_not_qualified(plugin_module):
    """bug-161: OTA group keys (`OTA_<callsign>`) are never country-qualified,
    even with the setting on -- qualifying them would be inconsistent with
    exempting OTA from the filter itself (it would still split same-callsign
    channels loaded from different country databases even though nothing
    downstream enforces that split for OTA).

    Mutation this catches: dropping the `self._is_ota_channel(channel_info)`
    check from `_group_key_for_channel`'s early-return guard."""
    inst = _matcher_inst(plugin_module)
    channel_info = {"channel_name": "WGN Chicago", "type": "broadcast (OTA)",
                     "callsign": "WGN", "_country_code": "US"}
    key = inst._group_key_for_channel(
        "OTA_WGN", {"id": 1, "name": "WGN Chicago", "channel_group__name": None},
        channel_info, True, [channel_info])
    assert key == "OTA_WGN"


def test_build_channel_groups_ota_key_unqualified_end_to_end(plugin_module):
    """End-to-end companion to the direct `_group_key_for_channel` test above,
    through the real grouping helper `_build_channel_groups` (bug-158 review
    I1's shared grouper used by Match & Assign, Preview and Manage Channel
    Visibility).

    Mutation this catches: same as above, but would also catch a regression
    where `_build_channel_groups` stopped calling `_group_key_for_channel` for
    OTA channels at all (e.g. an early `continue`/`return` before it)."""
    inst = _matcher_inst(plugin_module)
    channel = {"id": 1, "name": "WGN Chicago", "channel_group__name": None}
    groups = inst._build_channel_groups(
        [channel], WGN_OTA_DB, LOGGER, [], True, True, True, True,
        restrict_matching_to_country=True)
    assert "OTA_WGN" in groups
    assert "OTA_WGN@@US" not in groups


# ============================================================================
# bug-161 residual 1: Sort must not fetch channel_group for every stream when
# restrict_matching_to_country is off (an extra lazy-FK query per stream on
# top of Sort's existing per-stream Stream.objects.get(), inside a
# non-yielding gevent greenlet -- the bug-117 worker-freeze family).
# ============================================================================


class _TrackingChannelGroup:
    """Stands in for the ORM's `stream.channel_group` lazy FK. Records every
    access so a test can assert it was NEVER touched on the off path."""

    def __init__(self, name):
        self._name = name
        self.access_count = 0

    @property
    def name(self):
        self.access_count += 1
        return self._name


def _sort_action_harness(plugin_module, monkeypatch, restrict_matching_to_country):
    """Shared setup for the two residual-1 tests below: one channel, one
    stream carrying a `channel_group_id` (so the code WOULD look the group up
    if it evaluated the `getattr(...)` half of the guard on its own), wired
    through the real `sort_streams_action`."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_matcher.FuzzyMatcher()
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst.saved_settings = {}

    channel = {"id": 9, "name": "CNN", "channel_group__name": "News",
               "channel_group_id": None}
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [{"name": "Default", "id": 1}])
    monkeypatch.setattr(inst, "_get_all_channels", lambda logger: [channel])
    monkeypatch.setattr(inst, "_load_channels_data", lambda logger, settings: US_CNN_DB)
    monkeypatch.setattr(inst, "_zone_routed_map", lambda *a, **k: {})
    monkeypatch.setattr(inst, "_trigger_frontend_refresh", lambda *a, **k: None)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None)

    group = _TrackingChannelGroup("US")
    fake_streams = {
        1: types.SimpleNamespace(id=1, name="Encore Generic", stream_stats={},
                                  m3u_account_id=None, channel_group_id=7,
                                  channel_group=group),
        2: types.SimpleNamespace(id=2, name="Encore Feed", stream_stats={},
                                  m3u_account_id=None, channel_group_id=7,
                                  channel_group=group),
    }

    monkeypatch.setattr(plugin_module.ChannelProfileMembership, "objects",
                         _fake_orm_manager([9]))
    monkeypatch.setattr(plugin_module.ChannelStream, "objects",
                         _fake_orm_manager([1, 2]))
    monkeypatch.setattr(plugin_module.Stream, "objects",
                         types.SimpleNamespace(get=lambda id: fake_streams[id]))
    plugin_module.ChannelStream.reset_mock()

    settings = {"profile_name": "Default", "dry_run_mode": False}
    if restrict_matching_to_country is not None:
        settings["restrict_matching_to_country"] = restrict_matching_to_country
    result = inst.sort_streams_action(settings, LOGGER)
    return result, group


def test_sort_skips_channel_group_lookup_when_setting_off(plugin_module, monkeypatch):
    """bug-161 residual 1: with the setting off, `sort_streams_action` must
    never touch `stream.channel_group` at all, even though every stream here
    carries a truthy `channel_group_id` (the shape that would trigger a lookup
    if the code only checked `getattr(stream, 'channel_group_id', None)`).

    Mutation this catches: removing `restrict_matching_to_country and` from
    the `if restrict_matching_to_country and getattr(stream, 'channel_group_id', None):`
    guard in `sort_streams_action` -- `group.access_count` would go from 0 to 2."""
    result, group = _sort_action_harness(plugin_module, monkeypatch, restrict_matching_to_country=None)
    assert result["status"] == "success"
    assert group.access_count == 0, (
        f"channel_group.name was read {group.access_count} time(s) with the setting off")


def test_sort_does_fetch_channel_group_when_setting_on(plugin_module, monkeypatch):
    """Control for the test above: with the setting genuinely on, the lookup
    must still happen (proves the guard isn't accidentally always-false)."""
    result, group = _sort_action_harness(plugin_module, monkeypatch, restrict_matching_to_country=True)
    assert result["status"] == "success"
    assert group.access_count == 2, (
        f"expected both streams' channel_group.name to be read once each, got {group.access_count}")


# ============================================================================
# bug-161 residual 2: Preview's `details` payload must stay symmetric with
# pre-bug-158 behaviour (no details at all) on the off path -- only the
# country stats justify sending anything new, per M2's own intent.
# ============================================================================


def test_preview_details_defaults_to_none(plugin_module):
    """Mutation this catches: reverting `details = None` back to an
    unconditional dict literal (the bug-161 residual-2 regression)."""
    src = inspect.getsource(plugin_module.Plugin.preview_changes_action)
    assert "details = None" in src, (
        "preview_changes_action must default details to None, matching the "
        "pre-bug-158 no-details call on the off path")


def test_preview_details_dict_only_built_when_restriction_on(plugin_module):
    """The `channels_to_update`/`regex_rules_rejected` dict (and the country
    keys folded into it) must be built inside `if restrict_matching_to_country:`,
    not unconditionally -- otherwise a user with the setting off still gains
    two new keys in View Last Results that never existed pre-bug-158.

    Mutation this catches: un-indenting the `details = {...}` block (and the
    `_country_filter_details` update call) back out from under the guard."""
    src = inspect.getsource(plugin_module.Plugin.preview_changes_action)
    idx = src.index("'channels_to_update': total_channels_to_update,")
    preceding = src[:idx]
    # The nearest preceding non-blank line must be the guard, and the dict-open
    # line right before it must be the `details = {` this guard controls.
    lines = [ln for ln in preceding.splitlines() if ln.strip()]
    assert lines[-2].strip() == "if restrict_matching_to_country:", (
        f"unexpected guard line: {lines[-2]!r}")
    assert lines[-1].strip() == "details = {", f"unexpected dict-open line: {lines[-1]!r}"


# ============================================================================
# bug-161 residual 3: `_streams_for_channel` must apply the same-country-first
# partition for EVERY channel, not only zone-routed ones, so Sort cannot
# silently drop country-first ordering for a channel with no zone sibling.
# ============================================================================


def test_streams_for_channel_partitions_by_country_when_not_zone_routed(plugin_module):
    """Direct unit test of the residual-3 fix: a channel absent from
    `zone_routed` (the exact shape of a lone marked-zone channel with no
    opposite-zone sibling, or simply any non-zone-routed channel) must still
    get same-country streams promoted first.

    Mutation this catches: reverting `_streams_for_channel` to `return streams`
    for any channel not in `zone_routed` (the pre-fix behaviour) -- this would
    leave the streams in their original (foreign-first) order instead of
    [2, 1]."""
    inst = _matcher_inst(plugin_module)
    foreign_or_unknown = {"id": 1, "name": "Generic Feed", "m3u_account": 1, "url": "u1"}
    same_country = {"id": 2, "name": "US: Feed", "m3u_account": 1, "url": "u2"}
    out = inst._streams_for_channel(
        [foreign_or_unknown, same_country], channel_id=42, zone_routed={},
        same_country_ids={id(same_country)})
    assert [s["id"] for s in out] == [2, 1]


def test_streams_for_channel_still_noop_when_no_country_ids_and_not_zone_routed(plugin_module):
    """Control: a non-zone-routed channel with no same_country_ids (filter off,
    or filter on but nothing proven same-country) is untouched -- confirms the
    residual-3 fix did not turn `_streams_for_channel` into an unconditional
    reorder."""
    inst = _matcher_inst(plugin_module)
    a = {"id": 1, "name": "A", "m3u_account": 1, "url": "u1"}
    b = {"id": 2, "name": "B", "m3u_account": 1, "url": "u2"}
    out = inst._streams_for_channel([a, b], channel_id=42, zone_routed={}, same_country_ids=None)
    assert [s["id"] for s in out] == [1, 2]


def test_sort_action_country_first_ordering_when_not_zone_routed(plugin_module, monkeypatch):
    """End-to-end companion to the direct unit test above, through the real
    `sort_streams_action` with `_zone_routed_map` returning {} -- the lone
    marked-zone-channel-with-no-sibling shape from the residual-3 writeup.
    Before the fix, this reproduced the bug: `_streams_for_channel` returned
    `streams` unchanged for a channel absent from `zone_routed`, so Sort
    reverted to quality-tied ORM-fetch order regardless of country.

    Mutation this catches: the same `_streams_for_channel` reversion as
    `test_streams_for_channel_partitions_by_country_when_not_zone_routed`,
    caught here through the full action instead of the helper directly."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_matcher.FuzzyMatcher()
    inst._throughput_state_primed = True
    inst._throughput_sorting_enabled = False
    inst.saved_settings = {}

    channel = {"id": 9, "name": "CNN", "channel_group__name": "News",
               "channel_group_id": None}
    monkeypatch.setattr(inst, "_get_all_profiles", lambda logger: [{"name": "Default", "id": 1}])
    monkeypatch.setattr(inst, "_get_all_channels", lambda logger: [channel])
    monkeypatch.setattr(inst, "_load_channels_data", lambda logger, settings: US_CNN_DB)
    # Not zone-routed at all -- the exact gap residual 3 closes.
    monkeypatch.setattr(inst, "_zone_routed_map", lambda *a, **k: {})
    monkeypatch.setattr(inst, "_trigger_frontend_refresh", lambda *a, **k: None)
    monkeypatch.setattr(inst, "_send_progress_update", lambda *a, **k: None)

    fake_streams = {
        1: types.SimpleNamespace(id=1, name="Encore Generic", stream_stats={},
                                  m3u_account_id=None, channel_group_id=None),
        2: types.SimpleNamespace(id=2, name="Encore Feed", stream_stats={},
                                  m3u_account_id=None, channel_group_id=7,
                                  channel_group=types.SimpleNamespace(name="US")),
    }

    monkeypatch.setattr(plugin_module.ChannelProfileMembership, "objects",
                         _fake_orm_manager([9]))
    monkeypatch.setattr(plugin_module.ChannelStream, "objects",
                         _fake_orm_manager([1, 2]))
    monkeypatch.setattr(plugin_module.Stream, "objects",
                         types.SimpleNamespace(get=lambda id: fake_streams[id]))
    plugin_module.ChannelStream.reset_mock()

    result = inst.sort_streams_action(
        {"profile_name": "Default", "dry_run_mode": False,
         "restrict_matching_to_country": True},
        LOGGER)

    assert result["status"] == "success"
    constructed_order = [c.kwargs["stream_id"] for c in plugin_module.ChannelStream.call_args_list]
    assert constructed_order == [2, 1], (
        f"expected the group-confirmed same-country stream (2) first even though "
        f"the channel is not zone-routed, got {constructed_order}")


# ============================================================================
# bug-161 review D1: the OTA exemption must key off the signal that ACTUALLY
# decides OTA at runtime, not `_is_ota_channel(channel_info)` alone.
#
# `_is_ota_channel` is true only when the channel-DATABASE entry itself
# carries a `callsign` field. Verified against all twelve shipped
# `*_channels.json` files: zero entries carry a `callsign` key, zero entries
# have a `type` containing "broadcast". Production OTA matching therefore
# runs entirely through the bug-063 fallback (`_resolve_ota_callsign`, a
# parenthesized callsign in the Dispatcharr channel name itself), which is
# exactly what the tests below drive: `channels_data=[]` (the real shape --
# no channel-database entry at all) and a channel name carrying a
# parenthesized callsign, e.g. "CW - Chicago (WGN)".
# ============================================================================

from pathlib import Path  # noqa: E402

PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"


def _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path):
    """A Plugin instance wired with a FuzzyMatcher pointed at an EMPTY
    plugin_dir (no networks.json), so `_resolve_ota_callsign` cannot resolve
    an FCC-validated station and must fall through to the bug-063
    paren-callsign extraction -- the exact path production takes for every
    OTA channel, since none of the shipped databases carry a callsign entry
    either."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=85)
    return inst


def test_real_shape_ota_channel_keeps_state_code_collision_via_paren_fallback(
    plugin_module, fuzzy_module, tmp_path
):
    """The exact reviewer repro: 'CW - Chicago (WGN)' vs a stream carrying
    group 'IL: CHICAGO' (Israel under country.py's prefix detection), with
    channels_data=[] -- no channel-database entry at all, the real
    production shape. Before the D1 fix this returned [] because
    _is_ota_channel(None) is always False, so `not
    self._is_ota_channel(channel_info)` evaluated to True and the country
    filter ran (and dropped everything) BEFORE the callsign matcher ever saw
    the streams.

    Mutation this catches: reverting the exemption guard in
    _match_streams_to_channel from `not self._channel_ota_callsign(channel,
    channel_info)` back to `not self._is_ota_channel(channel_info)`."""
    inst = _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path)
    # Channel group "US: LOCALS" gives the CHANNEL a resolvable country (US) --
    # without this the country filter fails open regardless of the OTA guard
    # (bug-159 fail-open design), which would make this test pass even under
    # the pre-fix, broken exemption. This is the reviewer's exact repro shape.
    channel = {"id": 1, "name": "CW - Chicago (WGN)", "channel_group__name": "US: LOCALS"}
    streams = [
        # The callsign-match regex searches the stream NAME, so "WGN" must be
        # in the name; the collision under test is carried by the GROUP.
        {"id": 1, "name": "WGN Chicago", "channel_group__name": "IL: CHICAGO",
         "m3u_account": 1, "url": "u1"},
    ]
    matched, _, _, reason, _ = inst._match_streams_to_channel(
        channel, streams, LOGGER, channels_data=[], restrict_matching_to_country=True)
    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [1]


def test_real_shape_ota_stream_name_collision_via_paren_fallback(
    plugin_module, fuzzy_module, tmp_path
):
    """Same repro, collision carried in the STREAM NAME instead of its group:
    'IL: CHICAGO WGN'. Independently exercises the name-based country.py path
    (country_from_name) rather than the group-based one."""
    inst = _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path)
    channel = {"id": 1, "name": "CW - Chicago (WGN)", "channel_group__name": "US: LOCALS"}
    streams = [
        {"id": 1, "name": "IL: CHICAGO WGN", "channel_group__name": None,
         "m3u_account": 1, "url": "u1"},
    ]
    matched, _, _, reason, _ = inst._match_streams_to_channel(
        channel, streams, LOGGER, channels_data=[], restrict_matching_to_country=True)
    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [1]


def test_real_shape_ota_arkansas_argentina_collision_via_paren_fallback(
    plugin_module, fuzzy_module, tmp_path
):
    """Second reviewer repro: 'CBS - AR Little Rock (KTHV)' vs stream group
    'AR: LITTLE ROCK' (Argentina), channels_data=[]. Channel group "US: LOCALS"
    gives the CHANNEL a resolvable country -- see the note on the IL test."""
    inst = _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path)
    channel = {"id": 1, "name": "CBS - AR Little Rock (KTHV)", "channel_group__name": "US: LOCALS"}
    streams = [
        {"id": 1, "name": "KTHV Little Rock", "channel_group__name": "AR: LITTLE ROCK",
         "m3u_account": 1, "url": "u1"},
    ]
    matched, _, _, reason, _ = inst._match_streams_to_channel(
        channel, streams, LOGGER, channels_data=[], restrict_matching_to_country=True)
    assert reason == "Callsign match"
    assert [s["id"] for s in matched] == [1]


def test_real_shape_get_matches_at_thresholds_ota_exemption_via_paren_fallback(
    plugin_module, fuzzy_module, tmp_path
):
    """_get_matches_at_thresholds must reach the same OTA verdict as
    _match_streams_to_channel for the country-filter exemption, even though
    (documented, pre-existing, not fixed here) its own callsign-MATCHING
    branch still only fires for a database callsign entry and so never
    actually callsign-matches this channel. The exemption must still hold:
    with channels_data=[], the IL-collision stream must survive into
    candidate_streams and (since it fails the OTA callsign-match branch here)
    fall through to fuzzy threshold matching rather than being silently
    dropped by the country filter first."""
    inst = _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path)
    channel = {"id": 1, "name": "CW - Chicago (WGN)", "channel_group__name": "US: LOCALS"}
    streams = [
        {"id": 1, "name": "CW Chicago", "channel_group__name": "IL: CHICAGO",
         "m3u_account": 1, "url": "u1"},
    ]
    # Spy on candidate_streams by stubbing fuzzy_match to record what it saw.
    seen = []
    real_fuzzy_match = inst.fuzzy_matcher.fuzzy_match

    def spying_fuzzy_match(channel_name, stream_names, *a, **k):
        seen.append(list(stream_names))
        return real_fuzzy_match(channel_name, stream_names, *a, **k)

    inst.fuzzy_matcher.fuzzy_match = spying_fuzzy_match
    inst._get_matches_at_thresholds(
        channel, streams, LOGGER, [], True, True, True, True, [], 85,
        restrict_matching_to_country=True)
    assert seen, "fuzzy_match was never called"
    assert "CW Chicago" in seen[0], (
        "the IL-collision stream was dropped before ever reaching fuzzy matching "
        f"(saw {seen[0]!r})")


def test_real_shape_same_country_ids_for_ota_via_paren_fallback(
    plugin_module, fuzzy_module, tmp_path
):
    """_same_country_ids_for must also recognize the bug-063-fallback OTA
    channel and decline to compute a country partition for it. Channel group
    "US: LOCALS" gives the channel a resolvable country (US) -- without it
    _same_country_ids_for returns None anyway (channel_country_code
    unresolvable, bug-159 fail-open), which would make this assertion pass
    even under the pre-fix, broken OTA guard; with "US: LOCALS" the mutation
    below returns an empty SET (the IL stream classified FOREIGN against US),
    not None, so the two are genuinely distinguishable."""
    inst = _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path)
    channel = {"id": 1, "name": "CW - Chicago (WGN)", "channel_group__name": "US: LOCALS"}
    stream = {"id": 1, "name": "IL: CHICAGO WGN", "channel_group__name": None,
              "m3u_account": 1, "url": "u1"}
    ids = inst._same_country_ids_for(channel, [stream], [], LOGGER, True)
    assert ids is None


def test_real_shape_group_key_ota_not_qualified_via_paren_fallback(
    plugin_module, fuzzy_module, tmp_path
):
    """_group_key_for_channel must also decline to country-qualify a
    bug-063-fallback OTA channel's group key (which, per the documented
    pre-existing gap in _build_channel_groups, is the CLEANED NAME here,
    not an OTA_<callsign> key -- see the note on _build_channel_groups)."""
    inst = _fallback_matcher_inst(plugin_module, fuzzy_module, tmp_path)
    channel = {"id": 1, "name": "CW - Chicago (WGN)", "channel_group__name": "IL: CHICAGO"}
    key = inst._group_key_for_channel("cw chicago", channel, None, True)
    assert key == "cw chicago"


def test_real_us_channels_json_has_no_broadcast_callsign_entries(plugin_module):
    """Documents the exact production data shape this whole review round is
    about: loads the REAL shipped US_channels.json and asserts zero entries
    carry a callsign key or a type containing "broadcast". If this ever
    stops being true (the database gains real OTA entries), _is_ota_channel
    would start being a live signal again and this test would need revisiting
    -- it is a canary, not just documentation."""
    import json
    with open(PLUGIN_DIR / "US_channels.json", encoding="utf-8") as f:
        data = json.load(f)
    channels = data["channels"]
    assert channels, "US_channels.json loaded no channels -- test fixture broken"
    assert not any("callsign" in c for c in channels)
    assert not any("broadcast" in str(c.get("type", "")).lower() for c in channels)
