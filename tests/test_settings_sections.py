"""The settings form is divided into sections, and every setting sits under the right one.

Measured 2026-09-05 against the live plugin instance: the form served 49 fields
with only three information panels, and only ONE of those was a topical section
header. That header, "EPG-Aware Placeholder Matching", sat at field 17 of 49 and
nothing closed it, so the 32 settings after it read as part of a narrow optional
feature. The very next setting under it was Prioritize Quality Before Source,
which has nothing to do with EPG data.

That is worse than having no headers at all, because a heading that is never
closed actively misinforms. These tests lock the section boundaries so a setting
added later cannot silently land under the wrong heading.

The sibling plugin iptv_checker uses the same mechanism, an "info" field acting
as a heading, with 15 of them across 63 settings. Nine sections suit this
plugin, whose settings are fewer and more interdependent.
"""
import pytest

# The database field is built separately and can be any of three ids depending on
# whether the channel database files could be listed, so the scope section accepts
# whichever one the build produced.
DATABASE_FIELD_IDS = ("channel_database", "no_databases_found", "database_error")

# Each section heading and the setting that must come directly after it. Locking
# the BOUNDARY rather than the full membership means adding a setting inside a
# section does not need a test change, while moving a boundary does.
SECTION_BOUNDARIES = [
    ("_section_quickstart", "version_status"),
    ("_section_matching", "overwrite_streams"),
    ("_section_scope", DATABASE_FIELD_IDS),
    ("_section_name_preprocessing", "custom_aliases"),
    ("_section_notifications", "notify_enabled"),
    ("_section_epg_matching", "epg_placeholder_matching_enabled"),
    ("_section_stream_selection", "prioritize_quality"),
    ("_section_iptv_checker", "filter_dead_streams"),
    ("_section_scheduling", "scheduled_times"),
    ("_section_throughput", "enable_throughput_sorting"),
]


def _fields(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst.fields


def _ids(plugin_module):
    return [f.get("id") for f in _fields(plugin_module)]


def _section_ids(plugin_module):
    return [f.get("id") for f in _fields(plugin_module)
            if str(f.get("id", "")).startswith("_section_")]


def _fields_under(plugin_module, section_id):
    """The field ids between `section_id` and the next section heading."""
    ids = _ids(plugin_module)
    start = ids.index(section_id) + 1
    out = []
    for fid in ids[start:]:
        if str(fid).startswith("_section_"):
            break
        out.append(fid)
    return out


# --------------------------------------------------------------------------- #
# The sections exist and are in order
# --------------------------------------------------------------------------- #
def test_every_expected_section_heading_is_served(plugin_module):
    served = _section_ids(plugin_module)
    expected = [name for name, _first in SECTION_BOUNDARIES]
    missing = [s for s in expected if s not in served]
    assert not missing, f"missing section heading(s): {missing}"


def test_the_sections_appear_in_the_expected_order(plugin_module):
    served = _section_ids(plugin_module)
    expected = [name for name, _first in SECTION_BOUNDARIES]
    assert served == expected


@pytest.mark.parametrize("section_id,first_field", SECTION_BOUNDARIES)
def test_each_section_is_followed_by_the_setting_that_opens_it(
        plugin_module, section_id, first_field):
    """Locks where one section ends and the next begins."""
    ids = _ids(plugin_module)
    assert section_id in ids, f"{section_id} is not served at all"
    actual = ids[ids.index(section_id) + 1]
    if isinstance(first_field, tuple):
        assert actual in first_field, f"{section_id} is followed by {actual}"
    else:
        assert actual == first_field, f"{section_id} is followed by {actual}"


# --------------------------------------------------------------------------- #
# The defect that prompted this
# --------------------------------------------------------------------------- #
def test_the_epg_section_holds_only_epg_settings(plugin_module):
    """The measured defect: 32 unrelated settings sat under the EPG heading.

    Prioritize Quality Before Source directly followed the EPG settings with no
    heading between them, so it read as an EPG option. It is not one.
    """
    under = _fields_under(plugin_module, "_section_epg_matching")
    strays = [f for f in under if not str(f).startswith("epg_")]
    assert not strays, f"non-EPG settings under the EPG heading: {strays}"


def test_no_setting_sits_above_the_first_section_heading(plugin_module):
    ids = _ids(plugin_module)
    first_section = next(i for i, f in enumerate(ids)
                         if str(f).startswith("_section_"))
    assert first_section == 0, f"{ids[:first_section]} appear before any heading"


def test_the_dry_run_setting_sits_with_the_run_behaviour_settings(plugin_module):
    """It applies to manual actions AND scheduled runs, per its own help text.

    It used to sit alone between the IPTV Checker settings and the scheduling
    settings, which made it read as a scheduling option.
    """
    assert "dry_run_mode" in _fields_under(plugin_module, "_section_matching")


# --------------------------------------------------------------------------- #
# Rules for text this plugin shows the operator
# --------------------------------------------------------------------------- #
def test_no_section_body_contains_a_line_break(plugin_module):
    """An info panel body is one flowing paragraph; line breaks are not safe there."""
    offenders = [f.get("id") for f in _fields(plugin_module)
                 if str(f.get("id", "")).startswith("_section_")
                 and "\n" in (f.get("description") or "")]
    assert not offenders, offenders


def test_no_section_heading_uses_an_em_dash(plugin_module):
    """Standing instruction: no em dashes in copy the plugin shows the operator."""
    em_dash = chr(0x2014)
    offenders = []
    for f in _fields(plugin_module):
        fid = str(f.get("id", ""))
        if not fid.startswith("_section_"):
            continue
        if em_dash in (f.get("label") or "") or em_dash in (f.get("description") or ""):
            offenders.append(fid)
    assert not offenders, offenders


def test_section_headings_are_information_panels_that_store_nothing(plugin_module):
    """A heading must never become a stored setting: Dispatcharr never prunes one."""
    for f in _fields(plugin_module):
        if str(f.get("id", "")).startswith("_section_"):
            assert f.get("type") == "info", f["id"]
            assert "default" not in f, f["id"]
