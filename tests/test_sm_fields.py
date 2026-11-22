"""The Dispatcharr 0.34 settings form (sm_fields.py, Plugin.get_fields).

Dispatcharr 0.34 calls Plugin.get_fields and draws sections, pick lists and
tables; 0.33 and earlier read Plugin.fields and drop any field type they do not
know. These tests pin both halves: the flat form keeps every old id and no new
type, the 0.34 form maps every old id to a field, the pick lists and tables are
built from the saved text values, and the values a 0.34 save stores reach the
text settings every parser reads.
"""
import json
import logging
from unittest.mock import MagicMock

import pytest

NEW_TYPES = {"section", "multiselect", "table", "file"}


@pytest.fixture
def sm(plugin_module):
    return plugin_module.sm_fields


@pytest.fixture
def inst(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module.Plugin, "_get_channel_databases",
                        lambda self: [{"id": "US", "label": "US"}])
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._initialized = True
    return p


@pytest.fixture
def host_034(plugin_module, monkeypatch):
    monkeypatch.setitem(plugin_module._FORM_034_STATE, "supported", True)


@pytest.fixture
def host_033(plugin_module, monkeypatch):
    monkeypatch.setitem(plugin_module._FORM_034_STATE, "supported", False)


NAMES = {
    "channel_group_list": ["Sports", "News", "Bad, Name"],
    "stream_group_list": ["US| SPORTS", "UK| NEWS"],
    "m3u_source_list": ["Provider A", "Provider B"],
}


def _by_id(fields):
    return {f["id"]: f for f in fields if isinstance(f, dict) and "id" in f}


# --------------------------------------------------------------------------
# The flat form 0.33 reads is unchanged
# --------------------------------------------------------------------------
def test_flat_form_has_no_034_type_and_keeps_every_old_id(inst, sm):
    flat = inst.fields
    assert not [f["id"] for f in flat if f.get("type") in NEW_TYPES]
    assert not [f["id"] for f in flat if "section" in f or "rows" in f]
    ids = {f["id"] for f in flat}
    for old_id in sm.NEW_ID_BY_OLD_ID:
        assert old_id in ids, old_id


def test_every_flat_id_is_in_the_034_form_or_mapped(inst, sm):
    flat = inst.fields
    form_ids = set(_by_id(sm.build_form(flat, {}, NAMES, ["UK", "US"])))
    for f in flat:
        fid = f["id"]
        if sm.is_section_header(f):
            assert fid in form_ids
        elif fid in sm.NEW_ID_BY_OLD_ID:
            assert sm.NEW_ID_BY_OLD_ID[fid] in form_ids
            assert fid not in form_ids
        else:
            assert fid in form_ids, fid


def test_every_034_field_sits_in_a_declared_section(inst, sm):
    form = sm.build_form(inst.fields, {}, NAMES, ["UK"])
    sections = {f["id"] for f in form if f["type"] == "section"}
    assert len(sections) == 11
    for f in form:
        if f["type"] != "section":
            assert f.get("section") in sections, f["id"]
    collapsed = {f["id"] for f in form if f["type"] == "section" and f["collapsed"]}
    assert collapsed == set(sm.COLLAPSED_SECTIONS)


def test_034_ids_are_unique(inst, sm):
    form = sm.build_form(inst.fields, {}, NAMES, ["UK"])
    ids = [f["id"] for f in form]
    assert len(ids) == len(set(ids))


def test_help_text_is_one_paragraph_without_em_dash(inst, sm):
    form = sm.build_form(inst.fields, {}, NAMES, ["UK"])
    for f in form:
        for key in ("help_text", "description"):
            text = f.get(key) or ""
            assert "\n" not in text, f["id"]
            assert chr(0x2014) not in text, f["id"]


def test_long_help_is_split_into_titled_info_fields(inst, sm):
    form = _by_id(sm.build_form(inst.fields, {}, NAMES, ["UK"]))
    short, paragraphs = sm.HELP_PARAGRAPHS["restrict_matching_to_country"]
    assert form["restrict_matching_to_country"]["help_text"] == short
    assert form["restrict_matching_to_country_help_1"]["label"] == paragraphs[0][0]
    assert form["epg_placeholder_name_patterns"]["rows"] == 4


# --------------------------------------------------------------------------
# Pick lists
# --------------------------------------------------------------------------
def test_pick_list_default_comes_from_the_old_text(inst, sm):
    saved = {"selected_groups": "Sports, Gone Group", "selected_m3us": "Provider B, *",
             "scheduled_times": "0505,1312"}
    form = _by_id(sm.build_form(inst.fields, saved, NAMES, ["UK"]))
    groups = form["channel_group_list"]
    assert groups["type"] == "multiselect"
    assert groups["default"] == ["Sports", "Gone Group"]
    values = [o["value"] for o in groups["options"]]
    # A saved value the database no longer has is still an option, or the save
    # would be rejected; a name holding the comma separator is left out.
    assert "Gone Group" in values and "Bad, Name" not in values
    m3u = form["m3u_source_list"]
    assert m3u["default"] == ["Provider B", "*"]
    assert m3u["options"][0]["value"] == "*"
    times = form["scheduled_time_list"]
    assert times["default"] == ["0505", "1312"]
    assert "1312" in [o["value"] for o in times["options"]]
    assert len([o for o in times["options"] if o["value"] == "0505"]) == 1


def test_pick_list_options_keep_case_distinct_names(sm):
    # The plugin compares group names case-sensitively, so a saved 'sports'
    # must stay an option beside the database's 'Sports'.
    opts = [o["value"] for o in sm._options(["Sports"], ["sports"])]
    assert opts == ["Sports", "sports"] or opts == ["sports", "Sports"]


def test_stored_pick_list_values_are_options(inst, sm):
    saved = {"channel_group_list": ["Old Stored"]}
    form = _by_id(sm.build_form(inst.fields, saved, NAMES, ["UK"]))
    assert "Old Stored" in [o["value"] for o in form["channel_group_list"]["options"]]


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------
def test_alias_table_round_trip(sm):
    text = json.dumps({"Rai 1": ["RAI 1 Full HD", "Rai Uno"], "Rai 2": "Rai due"})
    rows = sm.alias_rows_from_text(text)
    assert rows == [{"channel": "Rai 1", "alias": "RAI 1 Full HD"},
                    {"channel": "Rai 1", "alias": "Rai Uno"},
                    {"channel": "Rai 2", "alias": "Rai due"}]
    back = json.loads(sm.alias_text_from_rows(rows))
    assert back == {"Rai 1": ["RAI 1 Full HD", "Rai Uno"], "Rai 2": ["Rai due"]}


def test_alias_rows_parse_like_the_text_box(plugin_module, sm):
    rows = [{"channel": "A", "alias": "a1"}, {"channel": "", "alias": ""},
            {"channel": "A", "alias": "a2"}, {"channel": "B", "alias": None}]
    usable, problem, skipped = plugin_module.Plugin._parse_custom_aliases(
        sm.alias_text_from_rows(rows))
    assert problem is None
    assert usable == {"A": ["a1", "a2"]}
    assert skipped == [("B", "no alias listed")]


def test_regex_table_round_trip_keeps_backslashes_and_spaces(sm):
    pairs = [[r"\s*▎\s*", " "], [r"\bVIP\b", ""]]
    rows = sm.regex_rows_from_text(json.dumps(pairs))
    assert rows[0] == {"find": r"\s*▎\s*", "replace": " "}
    assert json.loads(sm.regex_text_from_rows(rows)) == pairs


def test_regex_rows_feed_the_rule_parser(inst, sm):
    rows = [{"find": r"\bVIP\b", "replace": ""}]
    text = sm.regex_text_from_rows(rows)
    rules, report = inst._resolve_stream_regex_rules({"stream_name_regex_rules": text})
    assert len(rules) == 1 and report[0]["status"] == "ok"


def test_prefix_table_round_trip_parses_the_same(sm):
    from importlib import import_module
    country = import_module(sm.__name__.rsplit(".", 1)[0] + ".country")
    text = "NOW=UK, go=us # a comment\nBROKEN"
    rows = sm.prefix_rows_from_text(text)
    assert rows == [{"prefix": "NOW", "country": "UK"}, {"prefix": "GO", "country": "US"},
                    {"prefix": "BROKEN", "country": None}]
    assert (country.parse_prefix_country_overrides(sm.prefix_text_from_rows(rows))
            == country.parse_prefix_country_overrides(text))


def test_prefix_table_country_column_offers_saved_codes(inst, sm):
    saved = {"stream_prefix_countries": "NOW=UK, X=ZZ"}
    form = _by_id(sm.build_form(inst.fields, saved, NAMES, ["UK", "US"]))
    table = form["stream_prefix_country_rows"]
    assert table["type"] == "table" and table["label"] == ""
    country_col = [c for c in table["columns"] if c["id"] == "country"][0]
    assert {"UK", "US", "ZZ"} <= {o["value"] for o in country_col["options"]}
    assert form["stream_prefix_country_rows_about"]["type"] == "info"


@pytest.mark.parametrize("old_id, bad", [
    ("custom_aliases", "{not json"),
    ("custom_aliases", '[{"A": ["a"]}]'),
    ("stream_name_regex_rules", '[["only one"]]'),
    ("epg_title_cleanup_rules", '{"a": "b"}'),
])
def test_unconvertible_text_keeps_its_text_box(inst, sm, old_id, bad):
    logger = MagicMock()
    form = _by_id(sm.build_form(inst.fields, {old_id: bad}, NAMES, ["UK"], logger))
    assert old_id in form and form[old_id]["type"] == "text"
    assert sm.NEW_ID_BY_OLD_ID[old_id] not in form
    assert logger.warning.called


def test_shipped_regex_defaults_convert(inst, sm):
    form = _by_id(sm.build_form(inst.fields, {}, NAMES, ["UK"]))
    for new_id in ("stream_name_regex_rows", "epg_title_cleanup_rows",
                   "epg_channel_schedule_cleanup_rows"):
        assert form[new_id]["type"] == "table", new_id


# --------------------------------------------------------------------------
# Write-back
# --------------------------------------------------------------------------
def test_legacy_settings_writes_lists_back(sm):
    stored = {
        "selected_groups": "stale", "channel_group_list": ["Sports", "News"],
        "m3u_source_list": ["Provider B", "*"],
        "scheduled_time_list": ["0505", "1800"],
        "custom_alias_rows": [{"channel": "A", "alias": "a1"}],
        "stream_prefix_country_rows": [{"prefix": "NOW", "country": "UK"}],
        "stream_name_regex_rows": [],
    }
    out = sm.legacy_settings(stored)
    assert out["selected_groups"] == "Sports, News"
    assert out["selected_m3us"] == "Provider B, *"
    assert out["scheduled_times"] == "0505,1800"
    assert json.loads(out["custom_aliases"]) == {"A": ["a1"]}
    assert out["stream_prefix_countries"] == "NOW=UK"
    assert out["stream_name_regex_rules"] == ""
    assert sm.legacy_settings(out) == out          # idempotent
    assert stored["selected_groups"] == "stale"    # input untouched


def test_legacy_settings_ignores_non_list_values(sm):
    out = sm.legacy_settings({"selected_groups": "Sports", "channel_group_list": "x"})
    assert out["selected_groups"] == "Sports"


def test_default_round_trip_changes_nothing(inst, sm):
    saved = {"selected_groups": "Sports, News", "selected_m3us": "Provider B, *",
             "scheduled_times": "0505",
             "stream_prefix_countries": "NOW=UK",
             "custom_aliases": json.dumps({"A": ["a1", "a2"]})}
    form = _by_id(sm.build_form(inst.fields, saved, NAMES, ["UK"]))
    as_saved = {fid: f["default"] for fid, f in form.items() if "default" in f}
    out = sm.legacy_settings({**saved, **as_saved})
    for key in ("selected_groups", "selected_m3us", "scheduled_times",
                "stream_prefix_countries"):
        assert out[key] == saved[key], key
    assert json.loads(out["custom_aliases"]) == {"A": ["a1", "a2"]}


# --------------------------------------------------------------------------
# Plugin wiring
# --------------------------------------------------------------------------
def test_get_fields_builds_the_034_form(inst, monkeypatch, plugin_module):
    monkeypatch.setattr(plugin_module.Plugin, "_pick_list_names", staticmethod(lambda: NAMES))
    form = inst.get_fields({"settings": {"selected_groups": "Sports"}})
    ids = _by_id(form)
    assert ids["channel_group_list"]["default"] == ["Sports"]
    assert any(f["type"] == "section" for f in form)


def test_get_fields_falls_back_to_the_flat_form(inst, monkeypatch, plugin_module, caplog):
    def boom(*a, **k):
        raise RuntimeError("broken")
    monkeypatch.setattr(plugin_module.sm_fields, "build_form", boom)
    with caplog.at_level(logging.WARNING):
        form = inst.get_fields({"settings": {}})
    assert [f["id"] for f in form] == [f["id"] for f in inst.fields]


def test_pick_list_names_never_raises(inst, monkeypatch, plugin_module):
    broken = MagicMock()
    broken.objects.exclude.side_effect = RuntimeError("db down")
    monkeypatch.setattr(plugin_module, "Channel", broken)
    monkeypatch.setattr(plugin_module, "Stream", broken)
    names = plugin_module.Plugin._pick_list_names()
    assert names["channel_group_list"] == [] and names["stream_group_list"] == []


def test_write_back_only_on_a_034_host(inst, host_033):
    stored = {"selected_groups": "Typed On 033", "channel_group_list": ["Old 034"]}
    assert inst._legacy_settings(stored)["selected_groups"] == "Typed On 033"


def test_write_back_on_a_034_host(inst, host_034):
    stored = {"selected_groups": "stale", "channel_group_list": ["Sports"]}
    assert inst._legacy_settings(stored)["selected_groups"] == "Sports"


def test_settings_from_db_applies_the_write_back(inst, host_034, monkeypatch):
    import sys
    import types
    row = {"scheduled_times": "0500", "scheduled_time_list": ["0505"]}
    models = types.ModuleType("apps.plugins.models")
    cfg = MagicMock()
    cfg.objects.filter.return_value.values_list.return_value.first.return_value = row
    models.PluginConfig = cfg
    plugins_pkg = types.ModuleType("apps.plugins")
    plugins_pkg.__path__ = []
    monkeypatch.setitem(sys.modules, "apps.plugins", plugins_pkg)
    monkeypatch.setitem(sys.modules, "apps.plugins.models", models)
    assert inst._settings_from_db()["scheduled_times"] == "0505"


def test_run_applies_the_write_back(inst, host_034, monkeypatch):
    seen = {}

    def fake_validate(settings, logger):
        seen.update(settings)
        return {"status": "success", "message": "ok"}
    monkeypatch.setattr(inst, "validate_settings_action", fake_validate)
    monkeypatch.setattr(inst, "_initialize_fuzzy_matcher", lambda *a: None)
    monkeypatch.setattr(inst, "_build_alias_map", lambda *a: {})
    # run() reports usage after an action; on Linux the client would start a
    # real send, which the conftest guard refuses (CI failure 2026-11-21).
    monkeypatch.setattr(inst, "_report_usage", lambda *a, **k: None)
    inst.run("validate_settings", {"selected_groups": "stale", "channel_group_list": ["News"]})
    assert seen["selected_groups"] == "News"


def test_bug_report_redacts_the_034_source_list(inst):
    out = inst._redact_settings_for_report({"m3u_source_list": ["provider.example", "*"]})
    assert "provider.example" not in str(out)


def test_plugin_json_declares_manifest_version_1(plugin_module):
    from pathlib import Path
    data = json.loads((Path(plugin_module.__file__).parent / "plugin.json").read_text(encoding="utf-8"))
    assert data["manifest_version"] == 1
    # background_tasks: the long actions are "async" so the 0.34 task banner
    # shows their real result (Dispatcharr infers it from "async" anyway).
    assert set(data["capabilities"]) == {"outbound_network", "filesystem_write", "background_tasks"}
    assert data["fields"] == []
