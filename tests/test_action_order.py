"""The order the action buttons appear in, and that each one has a short label.

The order is the sequence an operator actually works through, so it is part of
the interface rather than an accident of how the list grew. Two orderings in
particular are load-bearing:

  Validate before Preview, because the quick-start text tells the operator to
  press them in that order.

  Match and Assign, then Probe Throughput, then Sort Alternate Streams. The
  probe measures download speed into a cache, and the sort reads that cache, so
  sorting before probing sorts against nothing. The previous order listed Sort
  before Probe.
"""


def _actions(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    return inst.actions


def _ids(plugin_module):
    return [a["id"] for a in _actions(plugin_module)]


EXPECTED = [
    # Before you run anything
    "validate_settings",
    "preview_changes",
    # The main run, in the order the buttons are pressed
    "add_streams_to_channels",
    "probe_throughput",
    "sort_streams",
    # Other matching and tidying
    "match_us_ota_only",
    "manage_channel_visibility",
    # Watch and review
    "view_check_progress",
    "view_last_results",
    "email_report_now",
    # Scheduling
    "update_schedule",
    "cleanup_periodic_tasks",
    # Tools and recovery
    "test_regex_rules",
    "check_stream_countries",
    "clear_csv_exports",
    "clear_operation_lock",
    "report_a_bug",
    # Not a button: Dispatcharr invokes this after an M3U source refreshes
    "on_m3u_refresh",
]


def test_the_action_order_is_the_intended_one(plugin_module):
    assert _ids(plugin_module) == EXPECTED


def test_no_action_was_lost_or_added_by_the_reordering(plugin_module):
    assert sorted(_ids(plugin_module)) == sorted(EXPECTED)
    assert len(_ids(plugin_module)) == len(set(_ids(plugin_module))), "duplicate action id"


def test_validate_comes_before_preview(plugin_module):
    ids = _ids(plugin_module)
    assert ids.index("validate_settings") < ids.index("preview_changes")


def test_the_probe_comes_before_the_sort_that_reads_its_cache(plugin_module):
    """The probe writes measured throughput into a cache and the sort reads it,
    so sorting first sorts against nothing."""
    ids = _ids(plugin_module)
    assert ids.index("probe_throughput") < ids.index("sort_streams")


def test_match_and_assign_comes_before_both(plugin_module):
    ids = _ids(plugin_module)
    assert ids.index("add_streams_to_channels") < ids.index("probe_throughput")


def test_the_event_handler_is_last_because_it_is_not_a_button(plugin_module):
    """Dispatcharr invokes it after an M3U source refreshes. It must stay
    registered and must keep a label or the action normaliser drops it, but
    nobody should press it."""
    assert _ids(plugin_module)[-1] == "on_m3u_refresh"


def test_every_PRESSABLE_action_has_a_short_button_label(plugin_module):
    """Without one the button falls back to the longer label, so it renders
    wider and worded differently from its neighbours.

    on_m3u_refresh is excluded on purpose. Omitting button_label is how that
    action is kept out of the settings page: Dispatcharr invokes it after an M3U
    source refreshes and nobody should press it. An existing test in
    tests/test_m3u_refresh_autorun.py asserts the key is absent, and adding one
    while reordering these buttons is exactly what that test caught.
    """
    missing = [a["id"] for a in _actions(plugin_module)
               if a["id"] != "on_m3u_refresh" and not a.get("button_label")]
    assert missing == [], f"actions with no button_label: {missing}"


def test_the_event_handler_still_has_NO_button_label(plugin_module):
    """Pins the hiding mechanism from the other direction."""
    handler = next(a for a in _actions(plugin_module) if a["id"] == "on_m3u_refresh")
    assert "button_label" not in handler


def test_every_action_still_has_a_label(plugin_module):
    """Dispatcharr's action normaliser silently drops an action with no label."""
    missing = [a["id"] for a in _actions(plugin_module) if not a.get("label")]
    assert missing == []


def test_no_em_dashes_in_any_button_label(plugin_module):
    for a in _actions(plugin_module):
        assert "—" not in a.get("button_label", "")
