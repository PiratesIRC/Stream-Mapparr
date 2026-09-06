"""The uncovered-placeholder scan, GitHub issue #43.

`epg_placeholder_name_patterns` only helps with the naming schemes the
operator already thought of, and nothing in the interface separates "no
placeholders here" from "your patterns match nothing". The reporter found two
whole families (`MAX #` at 128 streams, the largest they had) only by pulling
every stream name through the API by hand.

The scan groups every stream name by a digit-stripped template and reports the
recurring numbered families that no configured pattern covers. Two findings
from testing the idea against 25,323 local names are built in rather than
discovered later:

  A digit immediately followed by K is a resolution tag, not a slot number.
  Stripping it turned `4K` and `8K` into `#K` and merged 293 unrelated names
  into one false family.

  Not every numbered family is a placeholder (`UK: BBC RED BUTTON #`,
  `US: HULU ORIGINALS #`). A placeholder can only be resolved if its streams
  carry EPG data, so candidates are ranked by how many members carry an EPG
  identifier, which pushes the merely-numbered families down.

Report only. Nothing is added to the setting.
"""
import re
import string

from placeholder_scan import (
    DETAIL_LIMIT, MIN_DISTINCT_NUMBERS, template_of, suggested_pattern,
    scan_families, render_report,
)


def _pat(*patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]


# --------------------------------------------------------------------------- #
# The template
# --------------------------------------------------------------------------- #

def test_a_digit_run_becomes_one_slot():
    assert template_of("MAX 100") == ("MAX #", ("100",))
    assert template_of("Triller TV | Event 07") == ("Triller TV | Event #", ("07",))


def test_every_digit_run_is_a_slot_and_the_numbers_come_back_in_order():
    assert template_of("LIVE EVENT 04 - 11am") == ("LIVE EVENT # - #am", ("04", "11"))


def test_a_resolution_tag_is_not_a_slot():
    """`4K` and `8K` are resolution tags. Stripping them merged 293 unrelated
    names under one template on a real installation."""
    assert template_of("US: CINEMANIA 4K") == ("US: CINEMANIA 4K", ())
    assert template_of("PPV 12 | 4K") == ("PPV # | 4K", ("12",))
    # The K is upper-cased in the TEMPLATE only, so 4k and 4K are one family.
    # The name itself is never altered.
    assert template_of("Movie 8k") == ("Movie 8K", ())


def test_a_resolution_tag_needs_a_boundary_before_the_digit():
    """Only a digit run that STARTS at a word boundary and is immediately
    followed by K is left alone; a run glued to letters on the left is a slot."""
    assert template_of("X264K") == ("X#K", ("264",))


def test_a_name_with_no_digits_has_no_slot():
    assert template_of("CNN") == ("CNN", ())


def test_the_template_is_built_from_the_exact_name_not_a_normalised_one():
    """Case and spacing are kept so the suggested regex matches the real names."""
    assert template_of("  Max  100") == ("  Max  #", ("100",))


# --------------------------------------------------------------------------- #
# The suggested regex round-trips
# --------------------------------------------------------------------------- #

def test_the_suggested_pattern_matches_every_member_and_is_anchored():
    pattern = suggested_pattern("Triller TV | Event #")
    assert pattern == r"^Triller TV \| Event \d+$"
    compiled = re.compile(pattern, re.IGNORECASE)
    assert compiled.fullmatch("Triller TV | Event 07")
    assert compiled.fullmatch("triller tv | event 12")
    assert not compiled.fullmatch("Triller TV | Event 07 HD")


def test_a_kept_resolution_tag_survives_in_the_suggested_pattern():
    pattern = suggested_pattern("PPV # | 4K")
    assert pattern == r"^PPV \d+ \| 4K$"
    assert re.compile(pattern).fullmatch("PPV 12 | 4K")


def test_a_literal_hash_in_a_name_is_escaped_not_read_as_a_slot():
    """`#` is the slot marker in a template. A name that really contains one
    is stored with it escaped so it cannot be mistaken for a slot."""
    template, numbers = template_of("Event #7")
    assert numbers == ("7",)
    assert template != "Event ##"
    compiled = re.compile(suggested_pattern(template), re.IGNORECASE)
    assert compiled.fullmatch("Event #7")
    assert compiled.fullmatch("Event #12")
    assert not compiled.fullmatch("Event 7")


# --------------------------------------------------------------------------- #
# Which families are candidates
# --------------------------------------------------------------------------- #

def _streams(template, numbers, tvg="", start_id=1):
    out = []
    for i, n in enumerate(numbers):
        out.append({"id": start_id + i,
                    "name": template.replace("#", str(n)),
                    "tvg_id": tvg})
    return out


def test_a_recurring_numbered_family_is_a_candidate():
    streams = _streams("MAX #", [100, 101, 102, 103])
    families = scan_families(streams, [])
    assert [f["template"] for f in families] == ["MAX #"]
    fam = families[0]
    assert fam["count"] == 4
    assert fam["distinct_numbers"] == 4
    assert fam["example"] == "MAX 100"
    assert fam["suggested"] == r"^MAX \d+$"


def test_the_threshold_is_three_distinct_numbers():
    """Written as a literal, not derived from the constant. A test that sizes
    its own input from the threshold it is checking moves with the threshold
    and can never fail: that is how the first version of this file let the
    limit be lowered to 1 with every test still green."""
    assert MIN_DISTINCT_NUMBERS == 3
    assert scan_families(_streams("MAX #", [1, 2]), []) == []
    assert [f["template"] for f in scan_families(_streams("MAX #", [1, 2, 3]), [])]         == ["MAX #"]


def test_many_streams_sharing_one_number_are_not_a_family():
    """Five rows of `HBO 1` are five sources for one name, not five slots. The
    input is well over any plausible stream-count threshold, so this cannot
    pass by accident of a size rule."""
    streams = _streams("HBO #", [1] * 9)
    assert len(streams) == 9
    assert scan_families(streams, []) == []


def test_a_name_with_no_slot_is_never_a_family():
    streams = [{"id": i, "name": "CNN", "tvg_id": ""} for i in range(10)]
    assert scan_families(streams, []) == []


def test_a_family_fully_covered_by_a_pattern_is_reported_as_covered():
    streams = _streams("PPV EVENT #", [1, 2, 3, 4])
    families = scan_families(streams, _pat(r"^PPV EVENT \d+$"))
    assert families[0]["covered"] == 4
    assert families[0]["uncovered"] == 0


def test_a_partly_covered_family_says_how_many_members_miss():
    """A pattern written against one suffix shape silently misses a member
    with another shape. Partial coverage is the finding, so it is counted,
    not rounded to covered."""
    streams = _streams("PPV # |", [1, 2, 3]) + _streams("PPV #", [4, 5, 6])
    families = scan_families(streams, _pat(r"^PPV \d+ \|$"))
    by_template = {f["template"]: f for f in families}
    assert by_template["PPV # |"]["uncovered"] == 0
    assert by_template["PPV #"]["uncovered"] == 3


def test_coverage_uses_fullmatch_like_the_matcher_does():
    """The plugin decides eligibility with fullmatch, so an unanchored pattern
    that would search-match must not count as coverage here either."""
    streams = _streams("MAX # HD", [1, 2, 3])
    families = scan_families(streams, _pat(r"MAX \d+"))
    assert families[0]["uncovered"] == 3


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #

def test_families_whose_streams_carry_epg_identifiers_rank_first():
    """`BBC RED BUTTON #` is bigger, but its streams carry no EPG identifier, so
    a placeholder pattern for it could never resolve anything."""
    red_button = _streams("UK: BBC RED BUTTON #", list(range(1, 11)), tvg="")
    events = _streams("PPV EVENT #", [1, 2, 3], tvg="ppv.uk", start_id=100)
    families = scan_families(red_button + events, [])
    assert [f["template"] for f in families] == ["PPV EVENT #", "UK: BBC RED BUTTON #"]
    assert families[0]["with_epg_id"] == 3
    assert families[1]["with_epg_id"] == 0


def test_equal_epg_evidence_ranks_by_size():
    small = _streams("A #", [1, 2, 3], tvg="")
    big = _streams("B #", [1, 2, 3, 4, 5], tvg="", start_id=50)
    families = scan_families(small + big, [])
    assert [f["template"] for f in families] == ["B #", "A #"]


def test_a_blank_or_missing_identifier_does_not_count_as_epg_evidence():
    streams = [{"id": 1, "name": "X 1", "tvg_id": "  "},
               {"id": 2, "name": "X 2"},
               {"id": 3, "name": "X 3", "tvg_id": None}]
    assert scan_families(streams, [])[0]["with_epg_id"] == 0


def test_a_missing_name_is_skipped_not_fatal():
    streams = [{"id": 1, "name": None}, {"id": 2}] + _streams("Y #", [1, 2, 3])
    assert [f["template"] for f in scan_families(streams, [])] == ["Y #"]


# --------------------------------------------------------------------------- #
# The readout
# --------------------------------------------------------------------------- #

def test_the_report_leads_with_uncovered_families_and_gives_a_pasteable_regex():
    streams = (_streams("MAX #", [100, 101, 102], tvg="max.us")
               + _streams("PPV EVENT #", [1, 2, 3], tvg="ppv.us", start_id=50))
    families = scan_families(streams, _pat(r"^PPV EVENT \d+$"))
    text = render_report(families, total_streams=len(streams), pattern_count=1,
                         feature_enabled=True)
    assert "1 likely placeholder famil" in text
    assert r"^MAX \d+$" in text
    assert text.index("MAX #") < text.index("PPV EVENT #")
    assert "Streams scanned" in text


def test_families_carrying_no_epg_data_are_separated_not_mixed_in():
    """MEASURED on 25,068 live stream names: 131 families are uncovered and
    only 16 hold a stream carrying an EPG identifier. A pattern for one of the
    other 115 could never resolve anything, so listing all 131 together is the
    long, mostly unactionable report this feature exists to avoid."""
    with_epg = _streams("PPV #", [1, 2, 3], tvg="ppv.us")
    without = _streams("KARAOKE #", list(range(1, 21)), tvg="", start_id=50)
    text = render_report(scan_families(with_epg + without, []),
                         total_streams=23, pattern_count=0, feature_enabled=True)
    assert "pattern to paste" in text
    head, tail = text.split("carry no EPG data", 1)
    assert "PPV #" in head
    assert "KARAOKE #" in tail
    assert "pattern to paste" not in tail


def test_the_detailed_list_is_capped_and_says_how_many_it_left_out():
    """131 families in full would be unreadable, and a silent cut is worse than
    a cut the reader can see."""
    # Names must differ by LETTERS, not by digits: two names differing only in a
    # digit are the same family by construction, which is the point of the module.
    labels = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    streams = []
    for i, label in enumerate(labels[:DETAIL_LIMIT + 5]):
        streams += _streams("FAM %s #" % label, [1, 2, 3], tvg="x", start_id=100 * i)
    text = render_report(scan_families(streams, []), total_streams=len(streams),
                         pattern_count=0, feature_enabled=True)
    assert text.count("pattern to paste") == DETAIL_LIMIT
    assert "5 more" in text


def test_the_report_says_when_nothing_is_uncovered():
    streams = _streams("PPV EVENT #", [1, 2, 3])
    families = scan_families(streams, _pat(r"^PPV EVENT \d+$"))
    text = render_report(families, total_streams=3, pattern_count=1, feature_enabled=True)
    assert "No uncovered" in text


def test_the_report_warns_when_the_feature_is_off():
    """A pattern list that is never consulted covers nothing, whatever it says."""
    text = render_report([], total_streams=0, pattern_count=0, feature_enabled=False)
    assert "OFF" in text


def test_the_report_is_plain_ascii_even_for_a_name_that_is_not():
    """A file opened under another codepage turns any non-ASCII byte into
    mojibake, the same rule as the CSV preamble. The name here is the real
    shape: MEASURED on this installation, family templates carry a circled
    bullet and superscript letters. An earlier version of this test used only
    synthetic ASCII names, so it passed while the live report broke the rule."""
    bullet = chr(0x25C9)
    streams = _streams("UK: HIGH STREET TV # " + bullet, [1, 2, 3], tvg="x")
    text = render_report(scan_families(streams, []), total_streams=3,
                         pattern_count=0, feature_enabled=True)
    text.encode("ascii")
    assert chr(92) + "u25c9" in text


def test_an_escaped_pattern_still_matches_the_real_name():
    """The escape has to be correct, not merely printable. Python regular
    expressions accept the backslash-u form, so the suggestion a user
    pastes must match the name it was derived from."""
    name = "UK: HIGH STREET TV 4 " + chr(0x25C9)
    template, _ = template_of(name)
    pattern = suggested_pattern(template)
    pattern.encode("ascii")
    assert re.compile(pattern, re.IGNORECASE).fullmatch(name)


# --------------------------------------------------------------------------- #
# The action wrapper
# --------------------------------------------------------------------------- #

class _Logger:
    def _record(self, msg, *a, **k):
        pass
    info = debug = warning = error = _record


def _plugin(plugin_module, tmp_path, monkeypatch, streams):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    monkeypatch.setattr(P, "BUG_REPORT_DIR", str(tmp_path / "config"), raising=False)
    monkeypatch.setattr(P, "_get_all_streams", lambda self, logger: streams,
                        raising=False)
    return inst


def test_the_action_writes_the_readout_and_returns_its_path(
        plugin_module, tmp_path, monkeypatch):
    streams = _streams("MAX #", [100, 101, 102], tvg="max.us")
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, streams)
    result = plugin.scan_placeholder_names_action(
        {"epg_placeholder_matching_enabled": True,
         "epg_placeholder_name_patterns": ""}, _Logger())
    assert result["status"] == "success"
    assert result["file"].endswith("placeholder-name-scan.txt")
    with open(result["file"], encoding="utf-8") as handle:
        text = handle.read()
    assert r"^MAX \d+$" in text
    assert r"^MAX \d+$" in result["message"]


def test_the_action_resolves_patterns_even_when_the_feature_is_off(
        plugin_module, tmp_path, monkeypatch):
    """_resolve_epg_matching_settings returns no patterns when the toggle is
    off. The scan must not inherit that, or every family would report as
    uncovered the moment somebody switched the feature off."""
    streams = _streams("PPV EVENT #", [1, 2, 3], tvg="ppv.us")
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, streams)
    result = plugin.scan_placeholder_names_action(
        {"epg_placeholder_matching_enabled": False,
         "epg_placeholder_name_patterns": r"^PPV EVENT \d+$"}, _Logger())
    with open(result["file"], encoding="utf-8") as handle:
        text = handle.read()
    assert "No uncovered" in text
    assert "off" in result["message"]


def test_the_action_reports_an_unreadable_stream_list_as_an_error(
        plugin_module, tmp_path, monkeypatch):
    P = plugin_module.Plugin
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, [])

    def _boom(self, logger):
        raise RuntimeError("database is down")
    monkeypatch.setattr(P, "_get_all_streams", _boom, raising=False)
    result = plugin.scan_placeholder_names_action({}, _Logger())
    assert result["status"] == "error"
    assert "database is down" in result["error"]


def test_the_action_message_fits_a_toast(plugin_module, tmp_path, monkeypatch):
    streams = []
    for i in range(40):
        streams += _streams("A very long provider family name number %d #" % i,
                            [1, 2, 3], tvg="x", start_id=100 * i)
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, streams)
    result = plugin.scan_placeholder_names_action({}, _Logger())
    assert len(result["message"]) <= plugin_module.PluginConfig.TOAST_BUDGET_CHARS


# --------------------------------------------------------------------------- #
# Review findings, 2026-09-06. Each was reproduced before it was fixed.
# --------------------------------------------------------------------------- #

def test_a_character_above_the_basic_plane_is_escaped_in_the_wider_form():
    """The four-digit escape has a MINIMUM width, not a fixed one, so an emoji
    produced five hex digits and Python's regular expressions read only four.
    The pasted pattern then matched nothing and the family kept being reported
    as uncovered, with nothing saying why. Measured before the fix."""
    name = "MAX " + chr(0x1F600) + " 1"
    template, _ = template_of(name)
    pattern = suggested_pattern(template)
    pattern.encode("ascii")
    assert re.compile(pattern, re.IGNORECASE).fullmatch(name)


def test_a_backslash_before_a_digit_is_not_confused_with_a_literal_hash():
    """A literal hash is stored escaped. A bare backslash followed by a digit
    produced those same two characters, so two different names grouped into one
    family and the suggested pattern did not match either. Measured before the
    fix: A-backslash-1-B and A-hash-B both became the template A-backslash-hash-B."""
    backslash = chr(92)
    with_backslash = "A" + backslash + "1B"
    with_hash = "A#B"
    assert template_of(with_backslash)[0] != template_of(with_hash)[0]
    pattern = suggested_pattern(template_of(with_backslash)[0])
    assert re.compile(pattern).fullmatch(with_backslash)


def test_distinct_numbers_are_counted_within_one_slot_not_across_slots():
    """Two slots holding two values each produced four combinations and cleared
    a threshold of three, while neither slot held three different numbers. The
    report says "different numbers appear in its slot", so the count has to mean
    that."""
    names = ["S 1-1", "S 1-2", "S 2-1", "S 2-2"]
    streams = [{"id": i, "name": n, "tvg_id": ""} for i, n in enumerate(names)]
    assert scan_families(streams, []) == []
    names += ["S 3-1"]
    streams = [{"id": i, "name": n, "tvg_id": ""} for i, n in enumerate(names)]
    families = scan_families(streams, [])
    assert [f["distinct_numbers"] for f in families] == [3]


def test_the_resolution_tag_groups_regardless_of_the_case_of_the_k():
    """Patterns are compiled case-insensitively, so 4k and 4K are one family.
    Keeping the tag verbatim split it in two."""
    names = ["Sky 4k 1", "Sky 4K 2", "Sky 4k 3"]
    streams = [{"id": i, "name": n, "tvg_id": ""} for i, n in enumerate(names)]
    families = scan_families(streams, [])
    assert len(families) == 1
    assert families[0]["count"] == 3
    pattern = re.compile(families[0]["suggested"], re.IGNORECASE)
    assert all(pattern.fullmatch(n) for n in names)


def test_the_scan_yields_to_the_worker_while_it_walks_the_names():
    """The plugin runs under gevent, where a loop that never yields freezes the
    whole worker and every request on it (bug-117). The scan reads 25,000 names
    and applies up to 50 operator-supplied patterns to each."""
    calls = []
    streams = [{"id": i, "name": "N %d" % i, "tvg_id": ""} for i in range(1200)]
    scan_families(streams, [], on_yield=calls.append, yield_every=500)
    assert len(calls) >= 2


def test_an_over_long_name_is_skipped_and_counted_not_matched():
    """The pattern safety gate deliberately admits polynomial patterns on the
    promise that the runtime bounds the input. That bound has to exist here too."""
    stats = {}
    streams = [{"id": 1, "name": "X" * 600 + " 1", "tvg_id": ""}]
    streams += _streams("OK #", [1, 2, 3])
    families = scan_families(streams, [], max_name_len=500, stats=stats)
    assert [f["template"] for f in families] == ["OK #"]
    assert stats["skipped_long"] == 1


def test_a_tripped_time_budget_stops_the_scan_and_is_reported_as_partial():
    """Reporting a partial scan is honest. Freezing the worker, or silently
    reporting a family as uncovered because the run gave up on it, is not."""
    stats = {}
    streams = _streams("A #", [1, 2, 3]) + _streams("B #", [4, 5, 6], start_id=90)
    scan_families(streams, [], budget_seconds=0.0, stats=stats)
    assert stats["budget_tripped"] is True
    text = render_report([], total_streams=6, pattern_count=0, feature_enabled=True,
                         stats=stats)
    assert "did not finish" in text


def test_the_report_says_how_many_names_it_skipped():
    text = render_report([], total_streams=10, pattern_count=0, feature_enabled=True,
                         stats={"skipped_long": 4, "budget_tripped": False})
    assert "4" in text and "too long" in text


def test_the_detail_limit_is_twenty_five():
    """Pinned as a literal. The cap test above sizes its input from the constant,
    so lowering the constant moved the test with it: measured, DETAIL_LIMIT could
    be cut from 25 to 5 with the whole suite still green."""
    assert DETAIL_LIMIT == 25


def test_the_header_numbers_are_the_numbers_and_not_labels_alone():
    """This project has already shipped a CSV preamble whose lines were false for
    the life of the feature, because only the label was ever checked."""
    streams = _streams("A #", [1, 2, 3], tvg="x") + _streams("B #", [1, 2, 3],
                                                             start_id=40)
    families = scan_families(streams, _pat(r"^A \d+$"))
    text = render_report(families, total_streams=6, pattern_count=1,
                         feature_enabled=True)
    assert "Streams scanned                 : 6" in text
    assert "Numbered families found         : 2" in text
    assert "Placeholder patterns configured : 1" in text
    assert "Families no pattern covers      : 1" in text


def test_the_second_listing_is_capped_and_says_how_many_it_left_out():
    """On live data this is the 115-family list, the one most likely to run long.
    A silent cut is the thing this readout exists to avoid."""
    limit = DETAIL_LIMIT * 4
    labels = [a + b for a in string.ascii_uppercase for b in string.ascii_uppercase]
    streams = []
    for i, label in enumerate(labels[:limit + 3]):
        streams += _streams("NOEPG %s #" % label, [1, 2, 3], tvg="", start_id=100 * i)
    text = render_report(scan_families(streams, []), total_streams=len(streams),
                         pattern_count=0, feature_enabled=True)
    assert text.count("(3 streams)") == limit
    assert "3 more" in text


def test_a_family_survives_duplicate_rows_only_through_its_other_numbers():
    """Five rows of `HBO 1` plus `HBO 2` and `HBO 3` is a real family of three
    numbers carried seven times. The all-duplicates test alone cannot tell the
    implemented rule from one keyed on the whole name."""
    streams = _streams("HBO #", [1] * 5) + _streams("HBO #", [2, 3], start_id=60)
    families = scan_families(streams, [])
    assert families[0]["count"] == 7
    assert families[0]["distinct_numbers"] == 3


def test_regex_metacharacters_in_a_name_round_trip():
    """The escaping is hand-rolled rather than re.escape, because re.escape also
    escapes the space and the hash and would make every suggestion unreadable.
    Hand-rolled means it needs its own test."""
    for name in ["A (B) [C] {D} 1", "A.B*C+D?E 1", "A|B^C$D 1", "50% off 3"]:
        template, _ = template_of(name)
        pattern = suggested_pattern(template)
        assert re.compile(pattern, re.IGNORECASE).fullmatch(name), name


def test_a_suggestion_too_long_for_the_setting_is_flagged():
    """The setting refuses a pattern over 500 characters. A suggestion longer
    than that would be skipped in silence if pasted, so the readout says so
    rather than offering it as if it worked."""
    long_name = "X" * 520 + " 1"
    streams = [{"id": i, "name": "X" * 520 + " %d" % n, "tvg_id": "x"}
               for i, n in enumerate([1, 2, 3])]
    text = render_report(scan_families(streams, []), total_streams=3,
                         pattern_count=0, feature_enabled=True)
    assert "too long" in text.lower()
    assert len(long_name) > 500


def test_a_row_that_is_not_a_dict_is_skipped_rather_than_raising():
    """scan_families is called with whatever the database returned. An exception
    out of the walk reaches the operator as a stack trace, not as a readout."""
    streams = [None, "not a dict", 7] + _streams("Z #", [1, 2, 3])
    assert [f["template"] for f in scan_families(streams, [])] == ["Z #"]


def test_the_ordering_of_equally_ranked_families_is_stable():
    """131 families on live data. Two runs must list them in the same order or
    the readout cannot be compared with the previous one."""
    streams = (_streams("B #", [1, 2, 3], tvg="") + _streams("A #", [1, 2, 3],
                                                             tvg="", start_id=40))
    first = [f["template"] for f in scan_families(streams, [])]
    second = [f["template"] for f in scan_families(list(reversed(streams)), [])]
    assert first == second == ["A #", "B #"]


def test_the_suggestion_length_limit_matches_the_setting_that_enforces_it(plugin_module):
    """Two constants in two modules, because placeholder_scan.py has no plugin
    import. If they drift, the readout promises a pattern the setting rejects."""
    from placeholder_scan import SUGGESTION_MAX_LEN
    assert SUGGESTION_MAX_LEN == plugin_module.PluginConfig.REGEX_PATTERN_MAX_LEN


def test_the_action_says_when_nothing_numbered_was_found(
        plugin_module, tmp_path, monkeypatch):
    streams = [{"id": 1, "name": "CNN", "tvg_id": ""}]
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, streams)
    result = plugin.scan_placeholder_names_action({}, _Logger())
    assert "No numbered stream-name families" in result["message"]


def test_the_action_says_when_every_family_is_covered(
        plugin_module, tmp_path, monkeypatch):
    streams = _streams("PPV EVENT #", [1, 2, 3], tvg="x")
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, streams)
    result = plugin.scan_placeholder_names_action(
        {"epg_placeholder_matching_enabled": True,
         "epg_placeholder_name_patterns": r"^PPV EVENT \d+$"}, _Logger())
    assert "cover them all" in result["message"]


def test_the_action_counts_the_families_that_carry_epg_data_not_all_of_them(
        plugin_module, tmp_path, monkeypatch):
    """MEASURED on this installation: 131 uncovered families and 16 carrying an
    EPG identifier. Counting all of them reports a problem eight times larger
    than the one worth acting on."""
    with_epg = _streams("PPV #", [1, 2, 3], tvg="ppv.us")
    without = _streams("KARAOKE #", [1, 2, 3], tvg="", start_id=50)
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, with_epg + without)
    result = plugin.scan_placeholder_names_action({}, _Logger())
    assert "1 uncovered placeholder family carrying EPG data" in result["message"]
    assert "out of 2 uncovered in total" in result["message"]
    assert "PPV #" in result["message"]


def test_the_action_says_so_when_the_readout_could_not_be_written(
        plugin_module, tmp_path, monkeypatch):
    """The full readout exists ONLY in that file, so a run that could not write
    it produced nothing the operator can read. Reporting a plain success there
    is the failure this catch exists to prevent."""
    streams = _streams("MAX #", [1, 2, 3], tvg="x")
    plugin = _plugin(plugin_module, tmp_path, monkeypatch, streams)

    def _refuse(*args, **kwargs):
        raise OSError("read-only file system")
    monkeypatch.setattr(plugin_module.os, "makedirs", _refuse)
    result = plugin.scan_placeholder_names_action({}, _Logger())
    assert "file" not in result
    assert "could NOT be written" in result["message"]
