"""Channel Database as a pick list in the Dispatcharr 0.34 form.

The 0.33 form keeps the single-choice select (`channel_database`). The 0.34 form
draws a multiselect under the new id `channel_database_list`, and
`legacy_settings` writes it back into `channel_database` at run time as
`_all` (empty list), `_none`, or a comma list such as `US,UK`, which
`_resolve_enabled_databases` and `_build_alias_map` both read.
"""

import pytest


DATABASES = [
    {"id": "UK", "label": "UK - United Kingdom", "default": False},
    {"id": "US", "label": "US - United States", "default": True},
]


@pytest.fixture
def sm(plugin_module):
    return plugin_module.sm_fields


@pytest.fixture
def inst(plugin_module, monkeypatch):
    monkeypatch.setattr(plugin_module.Plugin, "_get_channel_databases",
                        lambda self: [dict(d) for d in DATABASES])
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p._initialized = True
    return p


def _field(sm, inst, saved):
    form = {f.get("id"): f for f in sm.build_form(inst.fields, saved, {}, ["UK", "US"])}
    assert "channel_database" not in form, "the old select must not be served beside the pick list"
    return form["channel_database_list"]


def test_flat_form_keeps_the_single_choice_select(inst):
    flat = {f.get("id"): f for f in inst.fields}
    assert flat["channel_database"]["type"] == "select"


def test_pick_list_offers_each_database_by_label_and_none(sm, inst):
    field = _field(sm, inst, {})
    assert field["type"] == "multiselect"
    options = {o["value"]: o["label"] for o in field["options"]}
    assert options["US"] == "US - United States"
    assert options["UK"] == "UK - United Kingdom"
    assert "_none" in options
    # Empty means every database, so there is no separate All entry to contradict it.
    assert "_all" not in options


@pytest.mark.parametrize("saved, expected", [
    ({"channel_database": "_all"}, []),
    ({"channel_database": "_none"}, ["_none"]),
    ({"channel_database": "UK"}, ["UK"]),
    ({"channel_database": "US,UK"}, ["US", "UK"]),
    ({}, ["US"]),  # never saved: the select's default
])
def test_default_comes_from_the_old_setting(sm, inst, saved, expected):
    assert _field(sm, inst, saved)["default"] == expected


def test_a_saved_code_with_no_database_file_stays_an_option(sm, inst):
    field = _field(sm, inst, {"channel_database_list": ["DE"]})
    assert "DE" in [o["value"] for o in field["options"]]


@pytest.mark.parametrize("value, expected", [
    ([], "_all"),
    (["US", "UK"], "US,UK"),
    (["_none"], "_none"),
    (["_none", "UK"], "UK"),
    (["_all", "US"], "_all"),
])
def test_legacy_settings_writes_the_list_back(sm, value, expected):
    out = sm.legacy_settings({"channel_database": "US", "channel_database_list": value})
    assert out["channel_database"] == expected


def test_legacy_settings_leaves_the_select_alone_without_the_list(sm):
    assert sm.legacy_settings({"channel_database": "UK"})["channel_database"] == "UK"


@pytest.mark.parametrize("setting, expected", [
    ("_all", None),
    ("_none", set()),
    ("UK", {"UK"}),
    ("us, uk", {"US", "UK"}),
    ("US,_none", {"US"}),
])
def test_resolver_reads_a_comma_list(inst, setting, expected):
    assert inst._resolve_enabled_databases({"channel_database": setting}) == expected


def test_alias_map_merges_every_selected_country(plugin_module, inst, monkeypatch):
    monkeypatch.setattr(plugin_module, "ALIAS_COUNTRY_OVERRIDES", {
        "UK": {"ZZ Alpha": ["ZZ Alpha UK"]},
        "US": {"ZZ Beta": ["ZZ Beta US"]},
    })
    both = inst._build_alias_map({}, "US,UK")
    assert "ZZ Alpha UK" in both["ZZ Alpha"]
    assert "ZZ Beta US" in both["ZZ Beta"]
    one = inst._build_alias_map({}, "UK")
    assert "ZZ Alpha" in one and "ZZ Beta" not in one
    assert "ZZ Alpha" not in inst._build_alias_map({}, "_all")


def test_empty_pick_list_says_it_means_every_database(sm, inst):
    assert _field(sm, inst, {"channel_database": "_all"})["placeholder"] == "Every database"
