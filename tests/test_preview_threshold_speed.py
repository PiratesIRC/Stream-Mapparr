"""Preview's per-channel threshold analysis was slow (measured 2026-11-22).

_get_matches_at_thresholds detected a station with _is_ota_channel, which is
always False in production (bug-161), so every station ran the fuzzy loop:
7 thresholds, and at each one every stream name cleaned again. On 29,243 real
stream names one channel took 11 to 14 seconds and 97% of it was
_clean_channel_name; a 166-channel Preview of group US: CBS estimated 30
minutes. Stations now take the call-letter path Match & Assign uses, and each
stream name is cleaned once per Preview run.
"""
import logging


def _plugin(plugin_module, fuzzy_module, tmp_path):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=95)
    p._alias_map = {}
    p._sort_streams_by_quality = lambda s: s
    p._deduplicate_streams = lambda s, allow_same_name_streams=False: s
    return p


def _call(p, name, streams, memo=None):
    return p._get_matches_at_thresholds(
        {"id": 1, "name": name}, streams, logging.getLogger("t"), [], True, True, True, True,
        [], 95, False, False, stream_country_memo={}, channel_info_cache={}, clean_name_memo=memo)


def test_a_station_takes_the_call_letter_path(plugin_module, fuzzy_module, tmp_path):
    p = _plugin(plugin_module, fuzzy_module, tmp_path)
    streams = [{"id": 1, "name": "US: CBS 44 (WEVV) EVANSVILLE HD", "m3u_account": 1},
               {"id": 2, "name": "US FOX 44 (WEVV DT2) Evansville (H)", "m3u_account": 1},
               {"id": 3, "name": "US: CBS 7 (WTHI) TERRE HAUTE", "m3u_account": 1}]
    result = _call(p, "CBS - IN Evansville (WEVV)", streams)
    assert list(result) == ["callsign_95"]
    assert [s["id"] for s in result["callsign_95"]["streams"]] == [1]   # FOX on DT2 dropped


def test_a_station_with_no_call_letter_match_falls_through(plugin_module, fuzzy_module, tmp_path):
    p = _plugin(plugin_module, fuzzy_module, tmp_path)
    streams = [{"id": 1, "name": "CBS Evansville", "m3u_account": 1}]
    seen = []
    real = p.fuzzy_matcher.fuzzy_match
    p.fuzzy_matcher.fuzzy_match = lambda c, names, *a, **k: seen.append(c) or real(c, names, *a, **k)
    _call(p, "CBS - IN Evansville (WEVV)", streams)
    assert seen, "the name search was skipped although no stream carries the call letters"


def test_each_stream_name_is_cleaned_once_per_run(plugin_module, fuzzy_module, tmp_path, monkeypatch):
    p = _plugin(plugin_module, fuzzy_module, tmp_path)
    streams = [{"id": i, "name": n, "m3u_account": 1} for i, n in enumerate(
        ["ESPN HD", "ESPN 2", "ESPN News", "Discovery Channel", "Discovery HD", "CNN"])]
    real = p._clean_channel_name
    calls = []

    def counting(name, *a, **k):
        calls.append(name)
        return real(name, *a, **k)

    monkeypatch.setattr(p, "_clean_channel_name", counting)
    memo = {}
    _call(p, "ESPN", streams, memo)
    _call(p, "Discovery Channel", streams, memo)
    # Each stream once, plus at most one cleanup of the best-matching name per
    # threshold per channel (7 thresholds x 2 channels). The old code cleaned
    # every stream at every threshold: 6 x 7 x 2 = 84 here.
    assert len(calls) <= len(streams) + 7 * 2, len(calls)


def test_results_are_unchanged_for_a_non_station(plugin_module, fuzzy_module, tmp_path):
    """The cache must not change which streams match at any threshold."""
    p = _plugin(plugin_module, fuzzy_module, tmp_path)
    streams = [{"id": i, "name": n, "m3u_account": 1} for i, n in enumerate(
        ["ESPN HD", "ESPN", "US: ESPN", "ESPN 2", "ESPNews", "CNN"])]
    fresh = {k: [s["id"] for s in v["streams"]] for k, v in _call(p, "ESPN", streams).items()}
    memo = {}
    _call(p, "CNN", streams, memo)
    reused = {k: [s["id"] for s in v["streams"]] for k, v in _call(p, "ESPN", streams, memo).items()}
    assert fresh and fresh == reused
