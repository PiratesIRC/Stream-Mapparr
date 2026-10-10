"""Guards that came out of the release-readiness review on 2026-09-05.

Each of these locks something that was found wrong that day, by a reviewer or by
rendering the output and reading it, rather than by reading the source.
"""
import ast
import io
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_SOURCE = os.path.join(ROOT, "Stream-Mapparr", "plugin.py")

EM_DASH = chr(0x2014)


def _fields(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return inst.fields


# --------------------------------------------------------------------------- #
# Copy the operator reads
# --------------------------------------------------------------------------- #
def test_no_em_dash_reaches_the_settings_form(plugin_module):
    """Standing instruction: no em dashes in copy this plugin shows the operator.

    Found by RENDERING the form and reading it, not by reading the source. Seven
    reached the page, one of them in a visible field label. The em dashes in code
    comments are not covered by the rule and are left alone.
    """
    offenders = []
    for field in _fields(plugin_module):
        for key in ("label", "help_text", "description", "placeholder"):
            text = field.get(key)
            if isinstance(text, str) and EM_DASH in text:
                offenders.append((field.get("id"), key))
    assert offenders == [], offenders


def test_no_em_dash_reaches_an_action_button(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    offenders = [(a.get("id"), key) for a in inst.actions
                 for key in ("label", "button_label", "description", "confirm")
                 if isinstance(a.get(key), str) and EM_DASH in a[key]]
    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# The report must render a setting the way the run treats it
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("value", [
    "true", "True", " TRUE ", "yes", "1", "on", "ON",
    "false", "no", "0", "", "anything else", None, True, False, 1, 0,
])
def test_a_setting_reads_in_the_report_the_way_the_run_treats_it(plugin_module, value):
    """These were two separate spellings of the same rule and they disagreed.

    The report renderer accepted "on" as true; every resolver in the plugin does
    not. So a setting stored as "on" printed Yes in the export preamble while the
    run itself treated it as off, and the preamble exists to record what the run
    did.
    """
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    behaves_as_true = inst._get_bool_setting({"k": value}, "k", False)
    rendered = plugin_module._yes_no(value)
    assert rendered == ("Yes" if behaves_as_true else "No"), (value, rendered)


# --------------------------------------------------------------------------- #
# Work that is off by default must cost nothing
# --------------------------------------------------------------------------- #
def test_no_directory_is_listed_when_export_retention_is_off(plugin_module,
                                                             tmp_path, monkeypatch):
    """The default is off, and the shared directory held 126 files.

    Listing it and stat-ing every entry, only to discard the result because the
    retention days are zero, is work done on every single export for nothing. The
    stat calls are not gevent-patched, so they block the worker.
    """
    monkeypatch.setattr(plugin_module.PluginConfig, "EXPORTS_DIR", str(tmp_path))
    listed = []
    monkeypatch.setattr(plugin_module.os, "listdir",
                        lambda path: listed.append(path) or [])

    removed = plugin_module.Plugin.__new__(plugin_module.Plugin)._prune_csv_exports(0)

    assert removed == 0
    assert listed == [], "the directory was listed even though retention is off"


# --------------------------------------------------------------------------- #
# The scheduler must not keep a stale timezone
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def plugin_ast():
    return ast.parse(open(PLUGIN_SOURCE, encoding="utf-8").read())


def test_the_scheduler_re_resolves_the_timezone_when_it_adopts_a_change(plugin_ast):
    """The loop re-reads its schedule every five minutes but resolved the
    timezone once, before the loop, and never again.

    The timezone comes from Dispatcharr's global setting, so a worker that never
    reconstructs the plugin object keeps firing on the old zone indefinitely.
    That is the same staleness the re-reading was written to remove, and the
    setting's own help text promises a change needs no container restart.
    """
    loop = next(n for n in ast.walk(plugin_ast)
                if isinstance(n, ast.FunctionDef) and n.name == "scheduler_loop")
    while_line = min(n.lineno for n in ast.walk(loop) if isinstance(n, ast.While))
    resolved_at = [n.lineno for n in ast.walk(loop) if isinstance(n, ast.Call)
                   and ast.unparse(n.func).endswith("_get_system_timezone")]
    assert any(line > while_line for line in resolved_at), \
        "the timezone is resolved only before the loop, so it can never change"
