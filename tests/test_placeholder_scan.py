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
    assert template_of("Movie 8k") == ("Movie 8k", ())


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
