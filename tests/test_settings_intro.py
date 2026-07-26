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


@pytest.mark.parametrize("fid", ["_section_quickstart"])
def test_no_em_dashes_in_user_facing_text(plugin_module, fid):
    """Project owner's standing style rule (2026-07-26)."""
    by = {f.get("id"): f for f in _fields(plugin_module)}
    text = by[fid]["label"] + by[fid]["description"]
    assert "—" not in text and "–" not in text


# --------------------------------------------------------------------------- #
# version checker removal
# --------------------------------------------------------------------------- #

def test_no_version_check_methods_remain(plugin_module):
    """The GitHub update check is gone: no method, no cache path, no constant."""
    P = plugin_module.Plugin
    assert not hasattr(P, "_check_version_update")
    assert not hasattr(P, "_get_latest_version")
    cfg = plugin_module.PluginConfig
    assert not hasattr(cfg, "VERSION_CHECK_CACHE_HOURS")
    assert not hasattr(cfg, "VERSION_CHECK_CACHE_FILE")


def test_fields_render_without_any_network_call(plugin_module, monkeypatch):
    """Building the settings form must never touch the network.

    `fields` is on Dispatcharr's per-request hot path, so a blocking call here
    stalls a worker (the bug-117 family). Any urlopen attempt fails this test.
    """
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("fields performed a network call")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.26.9999999"
    fields = inst.fields
    by = {f.get("id"): f for f in fields}
    assert by["version_status"]["label"] == "Current version: 1.26.9999999"


def test_version_line_is_static_text(plugin_module):
    """It reports the running version and makes no claim about being up to date."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.26.9999999"
    by = {f.get("id"): f for f in inst.fields}
    label = by["version_status"]["label"]
    for gone in ("Update available", "up to date", "unable to check", "update check failed"):
        assert gone.lower() not in label.lower()


# --------------------------------------------------------------------------- #
# Report a Bug button (was an info block; the owner asked for a button)
# --------------------------------------------------------------------------- #

def test_report_a_bug_is_an_action_not_a_field(plugin_module):
    assert "report_a_bug" in _action_ids(plugin_module)
    assert "_section_help" not in [f.get("id") for f in _fields(plugin_module)]


def test_report_a_bug_writes_a_file_and_returns_its_path(plugin_module, tmp_path, monkeypatch):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.26.9999999"
    monkeypatch.setattr(P, "BUG_REPORT_DIR", str(tmp_path), raising=False)

    import logging
    res = inst.report_a_bug_action({"profile_name": "a"}, logging.getLogger("t"))

    assert res["status"] == "success"
    assert "error" not in res
    body = pathlib.Path(res["file"]).read_text(encoding="utf-8")
    assert "github.com/PiratesIRC/Stream-Mapparr/issues" in body
    assert "1.26.9999999" in body
    assert "profile_name" in body


def test_report_a_bug_masks_secrets(plugin_module, tmp_path, monkeypatch):
    """webhook_url carries a Discord or Slack token; the file is meant to be
    pasted into a PUBLIC issue."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.26.9999999"
    monkeypatch.setattr(P, "BUG_REPORT_DIR", str(tmp_path), raising=False)

    import logging
    secret = "https://discord.com/api/webhooks/123456/SUPERSECRETTOKEN"
    res = inst.report_a_bug_action({"webhook_url": secret}, logging.getLogger("t"))
    body = pathlib.Path(res["file"]).read_text(encoding="utf-8")
    assert "SUPERSECRETTOKEN" not in body
    assert "redacted" in body


def test_report_a_bug_message_fits_the_toast(plugin_module, tmp_path, monkeypatch):
    """Dispatcharr's toast shows roughly 280 characters, clips from the MIDDLE
    with no ellipsis, and collapses newlines. The address must survive."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.26.9999999"
    monkeypatch.setattr(P, "BUG_REPORT_DIR", str(tmp_path), raising=False)

    import logging
    msg = inst.report_a_bug_action({}, logging.getLogger("t"))["message"]
    assert len(msg) <= 280, f"toast message is {len(msg)} chars"
    assert "\n" not in msg
    assert "github.com/PiratesIRC/Stream-Mapparr/issues" in msg


def test_report_a_bug_sets_error_when_it_cannot_write(plugin_module, monkeypatch):
    """A failure must be visible. `status` renders nowhere and `message` is the
    green success toast, so an unwritable path has to set `error`."""
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.26.9999999"
    monkeypatch.setattr(P, "BUG_REPORT_DIR", "/nonexistent\x00dir", raising=False)

    import logging
    res = inst.report_a_bug_action({}, logging.getLogger("t"))
    assert res["status"] == "error"
    assert res.get("error")
    assert "github.com/PiratesIRC/Stream-Mapparr/issues" in res["error"]
    assert "message" not in res
