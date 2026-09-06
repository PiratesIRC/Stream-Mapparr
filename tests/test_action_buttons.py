"""Action button colour, and the two action lists agreeing with each other.

Measured 2026-09-05. Two problems, found by comparing this plugin against the
sibling plugin iptv_checker, which colours all 23 of its actions consistently.

FIRST, colour did not track consequence. Match and Assign Streams was blue, the
same colour as View Last Results, which only reads a file. Match and Assign
replaces a channel's entire stream list when Overwrite Existing Streams is on,
so a channel can finish a run with fewer streams than it started with. Meanwhile
Clear CSV Exports, which deletes export files and no channel data at all, was
the only red button on the page. An operator scanning the buttons was being told
the opposite of the truth.

The rule adopted here, and the reason each colour means one thing:

  red     changes channel data in a way that can REMOVE something
  orange  changes data or clears state, but cannot remove a stream or a channel
  green   runs a normal operation that writes no channel data
  cyan    sends something outward, to an inbox or an issue tracker
  blue    reads and reports, changing nothing

Sort Alternate Streams is deliberately orange rather than red. It deletes and
rebuilds a channel's stream rows, but the SET of streams is unchanged and only
the order differs, so it cannot empty a channel. Match and Assign can.

SECOND, the two places actions are declared had drifted. Dispatcharr serves the
list in plugin.py for an enabled plugin, and plugin.json is the manifest. Their
button metadata disagreed in both directions: plugin.json gave preview_changes a
colour that plugin.py did not, and plugin.py gave email_report_now and
cleanup_periodic_tasks colours that plugin.json did not. Reading either one
alone gave a false picture of the interface, which is how the first problem was
originally described wrongly.
"""
import io
import json
import os

import pytest

MANIFEST = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Stream-Mapparr", "plugin.json")

# The event handler Dispatcharr invokes after an M3U refresh. It is not a button
# and must never grow one.
EVENT_HANDLER = "on_m3u_refresh"

EXPECTED_COLOURS = {
    # red: can remove streams or take channels off air
    "add_streams_to_channels": "red",
    "match_us_ota_only": "red",
    "manage_channel_visibility": "red",
    # orange: writes data or clears state, but removes nothing
    "sort_streams": "orange",
    "clear_csv_exports": "orange",
    "clear_operation_lock": "orange",
    "cleanup_periodic_tasks": "orange",
    # green: runs an operation, writes no channel data
    "update_schedule": "green",
    "probe_throughput": "green",
    # cyan: sends something outward
    "email_report_now": "cyan",
    "report_a_bug": "cyan",
    # blue: reads and reports
    "validate_settings": "blue",
    "preview_changes": "blue",
    "view_check_progress": "blue",
    "view_last_results": "blue",
    "test_regex_rules": "blue",
    "check_stream_countries": "blue",
    "scan_placeholder_names": "blue",
}


def _actions(plugin_module):
    """The list Dispatcharr actually serves for an ENABLED plugin."""
    return plugin_module.Plugin.__new__(plugin_module.Plugin).actions


def _manifest_actions():
    with open(MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)["actions"]


def _pressable(actions):
    return [a for a in actions if a.get("id") != EVENT_HANDLER]


# --------------------------------------------------------------------------- #
# Every button is coloured
# --------------------------------------------------------------------------- #
def test_every_pressable_action_has_a_button_colour(plugin_module):
    missing = [a["id"] for a in _pressable(_actions(plugin_module))
               if not a.get("button_color")]
    assert missing == [], f"actions with no button_color: {missing}"


def test_the_event_handler_has_no_button_colour(plugin_module):
    """It is invoked by Dispatcharr, never pressed, so it must not look pressable."""
    handler = next(a for a in _actions(plugin_module) if a["id"] == EVENT_HANDLER)
    assert "button_color" not in handler


# --------------------------------------------------------------------------- #
# Colour means one thing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("action_id,colour", sorted(EXPECTED_COLOURS.items()))
def test_the_action_carries_the_colour_its_consequence_calls_for(
        plugin_module, action_id, colour):
    action = next((a for a in _actions(plugin_module) if a["id"] == action_id), None)
    assert action is not None, f"{action_id} is not served"
    assert action.get("button_color") == colour


def test_red_is_reserved_for_actions_that_can_remove_something(plugin_module):
    """If red spreads to merely noisy actions it stops carrying any warning."""
    red = {a["id"] for a in _actions(plugin_module)
           if a.get("button_color") == "red"}
    assert red == {"add_streams_to_channels", "match_us_ota_only",
                   "manage_channel_visibility"}, red


def test_every_action_that_can_remove_something_also_asks_for_confirmation(plugin_module):
    """Colour is the glance; the dialog is the guard. A red button needs both."""
    for a in _actions(plugin_module):
        if a.get("button_color") == "red":
            assert a.get("confirm"), f"{a['id']} is red but has no confirm dialog"


# --------------------------------------------------------------------------- #
# The two declarations agree
# --------------------------------------------------------------------------- #
def test_the_manifest_and_the_served_list_hold_the_same_actions(plugin_module):
    served = sorted(a["id"] for a in _actions(plugin_module))
    manifest = sorted(a["id"] for a in _manifest_actions())
    assert served == manifest


@pytest.mark.parametrize("key", ["button_label", "button_color", "confirm",
                                 "button_variant"])
def test_the_manifest_and_the_served_list_agree_on_button_metadata(plugin_module, key):
    """They drifted in BOTH directions, so reading either alone misled.

    plugin.json is what the Plugin Hub and an operator browsing the repository
    see; plugin.py is what Dispatcharr renders. A reviewer who checks the wrong
    one draws the wrong conclusion about the interface, which is exactly what
    happened when this was first reviewed.
    """
    served = {a["id"]: a.get(key) for a in _actions(plugin_module)}
    manifest = {a["id"]: a.get(key) for a in _manifest_actions()}
    disagree = {k: (served.get(k), manifest.get(k))
                for k in served if served.get(k) != manifest.get(k)}
    assert disagree == {}, f"served vs manifest disagree on {key}: {disagree}"
