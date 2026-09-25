"""A `*` entry in M3U Sources means "every source not named here".

Requested by a user on 2026-09-24: they wanted streams from a free provider
first and any other provider when the free one has nothing, without naming every
other provider. Typing only the free provider made Match and Assign use ONLY
that provider, because in Match and Assign and Preview the list is a filter as
well as a priority order: streams from an unlisted source are dropped before
matching. Sort Alternate Streams already ranked unlisted sources last instead of
dropping them.

With `*` in the list, no source is dropped. Named sources rank in the order
written, and every unnamed source takes the position of the `*`. So
`Free Provider, *` means the free provider first and everything else after it,
and `*, Backup` means everything else first and Backup last. A source added
later is covered without editing the setting.

Without a `*` nothing changes: the list still filters Match and Assign and
Preview, and Sort still ranks unlisted sources last (priority 999).

Match and Assign, Preview and Sort all read the list through
_resolve_m3u_priorities, so they cannot disagree about the order.
"""
import inspect


NAME_TO_ID = {"Free Provider": 1, "Paid A": 2, "Paid B": 3, "Backup": 4}


def _resolve(plugin_module, raw, name_to_id=NAME_TO_ID):
    return plugin_module.Plugin._resolve_m3u_priorities(raw, name_to_id)


def _streams():
    return [
        {"id": 10, "m3u_account": 1},
        {"id": 20, "m3u_account": 2},
        {"id": 30, "m3u_account": 3},
        {"id": 40, "m3u_account": 4},
        {"id": 50, "m3u_account": None},
    ]


def _apply(plugin_module, raw):
    priorities = _resolve(plugin_module, raw)
    kept = plugin_module.Plugin._apply_m3u_priorities(_streams(), priorities)
    return {s["id"]: s["_m3u_priority"] for s in kept}


# --------------------------------------------------------------------------- #
# Resolving the list
# --------------------------------------------------------------------------- #
def test_a_name_then_wildcard_ranks_the_name_first_and_everything_else_next(plugin_module):
    r = _resolve(plugin_module, "Free Provider, *")
    assert r["priority_map"] == {1: 0}
    assert r["other_priority"] == 1
    assert r["names"] == ["Free Provider", "*"]
    assert r["missing"] == []


def test_a_name_after_the_wildcard_ranks_below_every_other_source(plugin_module):
    r = _resolve(plugin_module, "Free Provider, *, Backup")
    assert r["priority_map"] == {1: 0, 4: 2}
    assert r["other_priority"] == 1


def test_without_a_wildcard_there_is_no_other_priority(plugin_module):
    r = _resolve(plugin_module, "Paid B, Free Provider")
    assert r["priority_map"] == {3: 0, 1: 1}
    assert r["other_priority"] is None


def test_an_unknown_name_is_reported_and_takes_no_position(plugin_module):
    r = _resolve(plugin_module, "Nope, Free Provider, *")
    assert r["missing"] == ["Nope"]
    assert r["priority_map"] == {1: 0}
    assert r["other_priority"] == 1
    assert r["names"] == ["Free Provider", "*"]


def test_repeated_entries_count_once_at_their_first_position(plugin_module):
    r = _resolve(plugin_module, "Free Provider, *, Free Provider, *, Backup")
    assert r["priority_map"] == {1: 0, 4: 2}
    assert r["other_priority"] == 1
    assert r["names"] == ["Free Provider", "*", "Backup"]


def test_an_empty_box_resolves_to_nothing(plugin_module):
    for raw in ("", "   ", " , ,", None):
        r = _resolve(plugin_module, raw)
        assert r["priority_map"] == {} and r["other_priority"] is None
        assert r["names"] == [] and r["missing"] == []


# --------------------------------------------------------------------------- #
# Applying it to streams (the Match and Assign and Preview load path)
# --------------------------------------------------------------------------- #
def test_the_reported_case_keeps_every_source_with_the_free_one_first(plugin_module):
    assert _apply(plugin_module, "Free Provider, *") == {10: 0, 20: 1, 30: 1, 40: 1, 50: 1}


def test_without_a_wildcard_unlisted_sources_are_still_dropped(plugin_module):
    """Unchanged behaviour: the list filters when it has no `*`."""
    assert _apply(plugin_module, "Free Provider") == {10: 0}
    assert _apply(plugin_module, "Paid B, Free Provider") == {30: 0, 10: 1}


def test_a_lone_wildcard_keeps_everything_at_one_priority(plugin_module):
    assert _apply(plugin_module, "*") == {10: 0, 20: 0, 30: 0, 40: 0, 50: 0}


def test_wildcard_then_backup_puts_backup_last(plugin_module):
    assert _apply(plugin_module, "*, Backup") == {10: 0, 20: 0, 30: 0, 40: 1, 50: 0}


def test_a_source_added_later_is_covered_without_editing_the_list(plugin_module):
    name_to_id = dict(NAME_TO_ID, **{"New Source": 9})
    priorities = _resolve(plugin_module, "Free Provider, *", name_to_id)
    kept = plugin_module.Plugin._apply_m3u_priorities(
        [{"id": 90, "m3u_account": 9}, {"id": 10, "m3u_account": 1}], priorities)
    assert {s["id"]: s["_m3u_priority"] for s in kept} == {90: 1, 10: 0}


def test_the_sort_ordering_ranks_the_free_source_ahead(plugin_module):
    """End to end through the real quality sort, source before quality."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst._prioritize_quality = False
    priorities = _resolve(plugin_module, "Free Provider, *")
    streams = plugin_module.Plugin._apply_m3u_priorities(
        [{"id": 20, "m3u_account": 2, "name": "X", "stats": {"width": 1920, "height": 1080, "source_fps": 60}},
         {"id": 10, "m3u_account": 1, "name": "X", "stats": {"width": 1280, "height": 720, "source_fps": 30}}],
        priorities)
    ordered = inst._sort_streams_by_quality(streams)
    assert [s["id"] for s in ordered] == [10, 20]


# --------------------------------------------------------------------------- #
# Wiring and the operator-facing text
# --------------------------------------------------------------------------- #
def test_every_reader_of_the_setting_goes_through_the_resolver(plugin_module):
    load = inspect.getsource(plugin_module.Plugin.load_process_channels_action)
    sort = inspect.getsource(plugin_module.Plugin.sort_streams_action)
    for src in (load, sort):
        assert "_resolve_m3u_priorities(" in src
        assert "enumerate(valid_m3u_ids)" not in src
    assert "_apply_m3u_priorities(" in load


def test_the_sort_path_uses_the_wildcard_position_for_unnamed_sources(plugin_module):
    sort = inspect.getsource(plugin_module.Plugin.sort_streams_action)
    assert "_m3u_priority_for(" in sort
    p = plugin_module.Plugin
    with_wild = _resolve(plugin_module, "Free Provider, *, Backup")
    without = _resolve(plugin_module, "Free Provider")
    assert p._m3u_priority_for(1, with_wild) == 0
    assert p._m3u_priority_for(2, with_wild) == 1
    assert p._m3u_priority_for(4, with_wild) == 2
    assert p._m3u_priority_for(None, with_wild) == 1
    assert p._m3u_priority_for(2, without) == 999


def test_help_text_documents_the_wildcard(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    fields = {f["id"]: f for f in inst.fields}
    help_text = fields["selected_m3us"]["help_text"]
    assert "*" in help_text
    assert "every other" in help_text.lower()


def test_the_csv_header_spells_out_the_wildcard(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    header = inst._generate_csv_header_comment(
        {}, {"selected_m3us": ["Free Provider", "*"]})
    line = [ln for ln in header.splitlines() if ln.startswith("# Selected M3U Sources:")][0]
    assert line == "# Selected M3U Sources: Free Provider, * (every other source)"
