"""The operator-declared PREFIX=COUNTRY setting.

WHY IT IS A SETTING. A provider prefix is often a PLATFORM rather than a
country, and the same platform means different countries in different markets:
NOW is Sky's service in the United Kingdom and also in Italy. Shipping NOW=UK
would be right for one operator's feeds and wrong for another's, so the table is
empty by default and each installation declares its own.

MEASURED on a live installation 2026-08-09: all 388 NOW-prefixed streams already
resolved to UK through their GROUP label, and none sat in a non-UK group. So this
setting is insurance against a provider relabelling its groups, not a fix for a
present fault. It must therefore be incapable of changing an outcome that already
resolves, which is what the "consulted last" tests below pin.
"""
import sys


def _country(plugin_module):
    import importlib
    sys.path.insert(0, "Stream-Mapparr")
    return importlib.import_module("country")


def _inst(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

def test_lines_and_commas_are_both_accepted(plugin_module):
    c = _country(plugin_module)
    assert c.parse_prefix_country_overrides("NOW=UK\nGO=US") == {"NOW": "UK", "GO": "US"}
    assert c.parse_prefix_country_overrides("NOW=UK, GO=US") == {"NOW": "UK", "GO": "US"}


def test_case_is_normalised(plugin_module):
    c = _country(plugin_module)
    assert c.parse_prefix_country_overrides("now=uk") == {"NOW": "UK"}


def test_comments_and_blank_lines_are_ignored(plugin_module):
    c = _country(plugin_module)
    assert c.parse_prefix_country_overrides("# a note\n\nNOW=UK  # inline\n") == {"NOW": "UK"}


def test_a_malformed_line_is_skipped_not_raised_on(plugin_module):
    """This runs while resolving settings for every action. A typo must not stop
    matching."""
    c = _country(plugin_module)
    assert c.parse_prefix_country_overrides("NOWUK\n=UK\nNOW=\nNOW=UK") == {"NOW": "UK"}


def test_an_unknown_country_code_is_dropped(plugin_module):
    """An invented code would compare unequal to every real one and quietly mark
    every stream carrying that prefix as FOREIGN, which removes streams rather
    than doing nothing."""
    c = _country(plugin_module)
    assert c.parse_prefix_country_overrides("NOW=ZZ") == {}


def test_a_country_synonym_is_folded(plugin_module):
    """GB and UK name the same place."""
    c = _country(plugin_module)
    assert c.parse_prefix_country_overrides("NOW=GB") == {"NOW": "UK"}


def test_empty_and_non_string_input_give_an_empty_map(plugin_module):
    c = _country(plugin_module)
    for value in ("", None, 5, []):
        assert c.parse_prefix_country_overrides(value) == {}


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #

def test_the_anchored_prefix_forms_match(plugin_module):
    c = _country(plugin_module)
    ov = {"NOW": "UK"}
    for name in ("NOW: SKY SPORTS", "NOW| SKY SPORTS", "(NOW) SKY SPORTS", "NOW"):
        assert c.country_from_prefix_overrides(name, ov) == "UK", name


def test_the_word_inside_a_title_does_not_match(plugin_module):
    """A short entry that matched anywhere would reclassify unrelated streams.
    This is the same trap as a bare hostname regex matching ordinary words."""
    c = _country(plugin_module)
    ov = {"NOW": "UK"}
    for name in ("Right now on TV", "The Now Show", "Knowledge Channel"):
        assert c.country_from_prefix_overrides(name, ov) is None, name


def test_an_empty_map_matches_nothing(plugin_module):
    c = _country(plugin_module)
    assert c.country_from_prefix_overrides("NOW: SKY SPORTS", {}) is None


# --------------------------------------------------------------------------- #
# Where it sits in the resolution order
# --------------------------------------------------------------------------- #

def test_it_fills_a_gap(plugin_module):
    inst = _inst(plugin_module)
    stream = {"name": "NOW: SKY SPORTS", "channel_group__name": "SPORT"}
    assert inst._stream_country_code(stream) is None
    assert inst._stream_country_code(stream, {"NOW": "UK"}) == "UK"


def test_it_never_overrules_a_country_the_provider_stated(plugin_module):
    """Consulted LAST. An operator typo must not be able to reclassify streams
    the provider already labelled, which would remove them."""
    inst = _inst(plugin_module)
    stream = {"name": "NOW: SKY SPORTS", "channel_group__name": "UK| SPORT"}
    assert inst._stream_country_code(stream, {"NOW": "US"}) == "UK"


def test_the_group_is_consulted_as_well_as_the_name(plugin_module):
    inst = _inst(plugin_module)
    stream = {"name": "SKY SPORTS", "channel_group__name": "NOW| SPORT"}
    assert inst._stream_country_code(stream, {"NOW": "UK"}) == "UK"


def test_no_overrides_leaves_every_existing_result_unchanged(plugin_module):
    """The feature must be a no-op when unused."""
    inst = _inst(plugin_module)
    for stream in ({"name": "UK: MTV", "channel_group__name": "UK| GENERAL"},
                   {"name": "US: MTV", "channel_group__name": "US| GENERAL"},
                   {"name": "GO: MTV", "channel_group__name": "SPORT"}):
        assert inst._stream_country_code(stream) == inst._stream_country_code(stream, {})


# --------------------------------------------------------------------------- #
# Resolving the setting
# --------------------------------------------------------------------------- #

def test_the_setting_resolves_from_the_live_dict(plugin_module):
    inst = _inst(plugin_module)
    assert inst._resolve_prefix_country_overrides(
        {"stream_prefix_countries": "NOW=UK"}) == {"NOW": "UK"}


def test_an_absent_setting_gives_an_empty_map(plugin_module):
    inst = _inst(plugin_module)
    assert inst._resolve_prefix_country_overrides({}) == {}


def test_a_stored_none_is_treated_as_absent(plugin_module):
    """dict.get cannot tell absent from present-but-None, and Dispatcharr never
    prunes a stored setting when its field is removed."""
    inst = _inst(plugin_module)
    assert inst._resolve_prefix_country_overrides({"stream_prefix_countries": None}) == {}


def test_the_shipped_default_is_empty(plugin_module):
    """No mapping is correct for every installation."""
    assert plugin_module.PluginConfig.DEFAULT_STREAM_PREFIX_COUNTRIES == ""


def test_the_field_is_served_and_documents_the_format(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    field = next(f for f in inst.fields if f.get("id") == "stream_prefix_countries")
    assert "NOW=UK" in field.get("placeholder", "") + field.get("help_text", "")
