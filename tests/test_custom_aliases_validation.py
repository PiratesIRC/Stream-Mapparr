"""Validate Settings says so when the Custom Aliases box cannot be used.

Reported by a user on 2026-09-25: aliases "work only if I use aliases for one
channel". They had written three separate objects inside a list:

  [{"Rai 1 FHD": [...]}, {"Super Tennis FHD": [...]}, {"Rai 2 FHD": [...]}]

The setting must be ONE object with a key per channel. _build_alias_map
discards anything that is not an object, so every one of the three entries was
dropped, and the only trace was one warning line in the container log. A single
entry is usually typed without the brackets, which is already the right shape,
so it looked as though one channel worked and three did not.

Validate Settings now reports each way the box can be unusable as a WARNING, not
an error. The matcher carries on with the built-in aliases when the box is
unusable, so refusing to run would turn a formatting mistake into an outage.

The validator and the matcher read the box through ONE parser,
_parse_custom_aliases, so the two cannot disagree about which entries count.
"""
import inspect
import json


def _plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def _lines(plugin_module, raw):
    return _plugin(plugin_module)._validate_custom_aliases_setting({"custom_aliases": raw})


# --------------------------------------------------------------------------- #
# Nothing to say
# --------------------------------------------------------------------------- #
def test_an_empty_box_produces_no_line(plugin_module):
    assert _lines(plugin_module, "") == []
    assert _lines(plugin_module, "   ") == []
    assert _plugin(plugin_module)._validate_custom_aliases_setting({}) == []


def test_a_valid_object_reports_how_many_channels_it_covers(plugin_module):
    lines = _lines(plugin_module, json.dumps({"A": ["a1"], "B": "b1", "C": ["c1", "c2"]}))
    assert len(lines) == 1
    assert lines[0].startswith("✅")
    assert "3" in lines[0]


# --------------------------------------------------------------------------- #
# The reported case and the other whole-box failures
# --------------------------------------------------------------------------- #
def test_the_reported_list_of_objects_is_a_warning_naming_the_fix(plugin_module):
    raw = ('[{"Rai 1 FHD":["Rai 1 FHD", "RAI 1 Full HD"]},'
           '{"Super Tennis FHD":["Super Tennis FHD", "Super Tennis FULL HD"]},'
           '{"Rai 2 FHD":["Rai 2 FHD", "Rai due FHD"]}]')
    lines = _lines(plugin_module, raw)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("⚠")
    assert "list" in line.lower()
    assert "none" in line.lower()
    assert "one object" in line.lower()


def test_invalid_json_is_a_warning_with_the_position(plugin_module):
    lines = _lines(plugin_module, '{"A": ["a1"],}')
    assert len(lines) == 1
    assert lines[0].startswith("⚠")
    assert "json" in lines[0].lower()
    assert "line 1" in lines[0]


def test_a_bare_string_or_number_is_a_warning(plugin_module):
    for raw in ('"just a string"', "42", "null"):
        lines = _lines(plugin_module, raw)
        assert len(lines) == 1, raw
        assert lines[0].startswith("⚠"), raw
        assert "object" in lines[0].lower(), raw


# --------------------------------------------------------------------------- #
# Some entries used, some ignored
# --------------------------------------------------------------------------- #
def test_entries_the_matcher_skips_are_named(plugin_module):
    raw = json.dumps({"Good": ["g1"], "Numeric": 5, "Blank": ["", "  "], "Nested": {"x": 1}})
    lines = _lines(plugin_module, raw)
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("⚠")
    assert "1 of 4" in line
    for name in ("Numeric", "Blank", "Nested"):
        assert name in line
    assert "Good" not in line


def test_a_long_list_of_skipped_entries_is_capped(plugin_module):
    raw = json.dumps({f"Channel {i}": 5 for i in range(10)})
    line = _lines(plugin_module, raw)[0]
    assert "Channel 0" in line
    assert "Channel 9" not in line
    assert "7 more" in line


# --------------------------------------------------------------------------- #
# Validator and matcher agree, and the check is wired in
# --------------------------------------------------------------------------- #
def test_the_count_the_validator_reports_is_what_the_matcher_merges(plugin_module):
    p = _plugin(plugin_module)
    raw = json.dumps({"Zzz Only Custom 1": ["z1"], "Zzz Only Custom 2": 7,
                      "Zzz Only Custom 3": ["z3"]})
    alias_map = p._build_alias_map({"custom_aliases": raw}, None)
    merged = [k for k in alias_map if k.startswith("Zzz Only Custom")]
    assert sorted(merged) == ["Zzz Only Custom 1", "Zzz Only Custom 3"]
    assert "2 of 3" in _lines(plugin_module, raw)[0]


def test_validate_plugin_settings_runs_the_alias_check(plugin_module):
    src = inspect.getsource(plugin_module.Plugin._validate_plugin_settings)
    assert "_validate_custom_aliases_setting(settings)" in src


def test_a_problem_is_a_warning_not_an_error(plugin_module):
    """An error would make Match and Assign refuse to load channels, while the
    matcher itself carries on without the custom aliases. The check must not
    stop a run the matcher would have completed."""
    for raw in ("[{}]", "{bad", "42", json.dumps({"A": 5})):
        for line in _lines(plugin_module, raw):
            assert not line.startswith("❌"), raw
