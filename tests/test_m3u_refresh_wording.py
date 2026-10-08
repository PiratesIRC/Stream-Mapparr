"""The auto-match wording must say it runs only after a SUCCESSFUL M3U refresh.

Dispatcharr 0.32.0 returns before emitting m3u_refresh when a refresh ends in
ERROR, so the plugin's text must not claim it runs after every refresh.
"""
import json
import pathlib

MANIFEST = (pathlib.Path(__file__).resolve().parent.parent
            / "Stream-Mapparr" / "plugin.json")


def _plugin_action(plugin_module, action_id):
    for a in plugin_module.Plugin.actions:
        if a["id"] == action_id:
            return a
    raise AssertionError(f"action {action_id!r} not in plugin.py actions")


def _manifest_action(action_id):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for a in manifest.get("actions", []):
        if a["id"] == action_id:
            return a
    raise AssertionError(f"action {action_id!r} not in plugin.json")


def _auto_match_field(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    for f in inst.fields:
        if f.get("id") == "auto_match_on_m3u_refresh":
            return f
    raise AssertionError("auto_match_on_m3u_refresh field not found")


def test_action_description_says_successful_and_error_does_not_trigger(plugin_module):
    desc = _plugin_action(plugin_module, "on_m3u_refresh")["description"]
    assert "successful" in desc.lower()
    assert "error" in desc.lower()
    assert "does not trigger" in desc


def test_manifest_action_description_matches_plugin_py(plugin_module):
    py_desc = _plugin_action(plugin_module, "on_m3u_refresh")["description"]
    json_desc = _manifest_action("on_m3u_refresh")["description"]
    assert json_desc == py_desc


def test_field_help_text_says_successful(plugin_module):
    help_text = _auto_match_field(plugin_module)["help_text"]
    assert "successful" in help_text.lower()
    # The rest of the string must survive the wording change.
    assert "Requires a Profile to be selected" in help_text
    assert "coalesced" in help_text or "single match" in help_text
