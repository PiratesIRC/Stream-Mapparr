"""Quick Start / Help info blocks and action registration (tasks #8, #9; bug-162).

The action tests exist because `preview_changes` was declared in plugin.json but
missing from the plugin.py `actions` list, so the released build served no Preview
button at all. Verified against the deployed 1.26.1992013 instance before fixing.
"""

import json
import pathlib
import re

import pytest

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "Stream-Mapparr"


def _fields(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst.fields


def _action_ids(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    return [a["id"] for a in inst.actions]


# --------------------------------------------------------------------------- #
# bug-162: registration must live in plugin.py, not only in the manifest
# --------------------------------------------------------------------------- #

def test_preview_changes_is_served_as_an_action(plugin_module):
    """The dry run every doc tells users to run first must have a button.

    Manifest actions are ignored for an enabled plugin, so a manifest-only entry
    renders nothing. Confirmed live on 1.26.1992013: 14 actions served, no preview.
    """
    assert "preview_changes" in _action_ids(plugin_module)


def test_every_dispatchable_action_is_declared(plugin_module):
    """Guards the exact drift that hid Preview for a whole release.

    `run()` dispatches through a name -> handler map. Any key in that map which
    is not in `actions` has working code and no button, which is precisely how
    preview_changes went missing. load_process_channels is the one deliberate
    exception: Preview and Match & Assign both call it as their first step, and
    _INTERNAL_PROGRESS_ACTIONS suppresses its toast, so it is intentionally
    unexposed.
    """
    source = (PLUGIN_DIR / "plugin.py").read_text(encoding="utf-8")
    dispatched = set(re.findall(r'"([a-z_]+)":\s*self\.\1_action\b', source))
    assert dispatched, "could not locate the action dispatch map"

    internal = {"load_process_channels"}
    declared = set(_action_ids(plugin_module))
    orphans = dispatched - declared - internal
    assert not orphans, f"dispatchable but no button: {sorted(orphans)}"
    assert "load_process_channels" not in declared


def test_manifest_and_code_actions_agree(plugin_module):
    """plugin.json and plugin.py must declare the same action set.

    They diverged silently before (bug-162): the manifest carried
    load_process_channels and preview_changes, the code carried
    cleanup_periodic_tasks, and the labels disagreed.
    """
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    manifest_ids = [a["id"] for a in manifest["actions"]]
    assert sorted(manifest_ids) == sorted(_action_ids(plugin_module))


def test_manifest_and_code_labels_agree(plugin_module):
    manifest = json.loads((PLUGIN_DIR / "plugin.json").read_text(encoding="utf-8"))
    m = {a["id"]: a.get("label") for a in manifest["actions"]}
    P = plugin_module.Plugin
    inst = P.__new__(P)
    for a in inst.actions:
        assert m[a["id"]] == a.get("label"), f"label drift on {a['id']}"


# --------------------------------------------------------------------------- #
# tasks #8 / #9: the two info blocks
# --------------------------------------------------------------------------- #

def test_quick_start_is_the_first_field(plugin_module):
    ids = [f.get("id") for f in _fields(plugin_module)]
    assert ids[0] == "_section_quickstart"


def test_help_block_follows_quick_start(plugin_module):
    ids = [f.get("id") for f in _fields(plugin_module)]
    assert ids[1] == "_section_help"


def test_both_blocks_are_info_type_with_text(plugin_module):
    """`info` is the only field type that renders persistently. An action would
    show a toast that auto-closes in about 4 seconds and truncates."""
    by = {f.get("id"): f for f in _fields(plugin_module)}
    for fid in ("_section_quickstart", "_section_help"):
        assert by[fid].get("type") == "info"
        assert by[fid].get("label")
        assert len(by[fid].get("description", "")) > 80


def test_help_block_carries_the_issues_url(plugin_module):
    by = {f.get("id"): f for f in _fields(plugin_module)}
    assert "github.com/PiratesIRC/Stream-Mapparr/issues" in by["_section_help"]["description"]


def test_quick_start_only_names_actions_that_exist(plugin_module):
    """The original Quick Start draft named Preview, which had no button. If an
    action is renamed or dropped, this fails instead of shipping a lie."""
    by = {f.get("id"): f for f in _fields(plugin_module)}
    text = by["_section_quickstart"]["description"]
    P = plugin_module.Plugin
    inst = P.__new__(P)
    labels = {a["id"]: a.get("label", "") for a in inst.actions}

    def bare(label):
        return "".join(c for c in label if c.isascii()).strip()

    for aid in ("validate_settings", "preview_changes", "add_streams_to_channels",
                "sort_streams", "view_check_progress", "view_last_results"):
        name = bare(labels[aid]).replace(" (Dry Run)", "")
        assert name and name in text, f"Quick Start does not name {aid} ({name!r})"


@pytest.mark.parametrize("fid", ["_section_quickstart", "_section_help"])
def test_no_em_dashes_in_user_facing_text(plugin_module, fid):
    """Project owner's standing style rule (2026-07-26)."""
    by = {f.get("id"): f for f in _fields(plugin_module)}
    text = by[fid]["label"] + by[fid]["description"]
    assert "—" not in text and "–" not in text
