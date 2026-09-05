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
BADGE_SCRIPTS = [os.path.join(ROOT, "scripts", "update_streams_matched_badge.py"),
                 os.path.join(ROOT, "scripts", "update_streams_matched_badge.ps1")]

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
# Nothing published may name the machine it runs on
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", BADGE_SCRIPTS)
def test_the_badge_scripts_name_no_literal_machine_path(path):
    """These are committed to a PUBLIC repository.

    The first version of them hard-coded the Python, Docker and GitHub CLI paths,
    which names the Windows account. The publish audit caught it and was
    overridden with allow-list entries whose stated reason, that the sibling
    plugin publishes the same literal paths, was untrue: that plugin builds them
    from the environment for exactly this reason. The exemptions were removed and
    the paths are now built from LOCALAPPDATA and ProgramFiles.
    """
    text = open(path, encoding="utf-8").read()
    drive_prefix = "C:" + chr(92)
    offenders = [line.strip() for line in text.splitlines()
                 if drive_prefix in line or "C:/Users" in line]
    assert offenders == [], offenders


def test_the_powershell_wrapper_does_not_abort_on_a_line_of_stderr():
    """Windows PowerShell 5.1 turns a native command's stderr into an error
    record, and under 'Stop' that record terminates the pipeline. Measured: a
    process writing one line to stderr and exiting 0 throws. The wrapper then
    logged that the script had failed before running, which was untrue, threw
    away its real output, and reported failure to Task Scheduler for a run that
    had succeeded.
    """
    text = open(BADGE_SCRIPTS[1], encoding="utf-8").read()
    assert "2>&1" in text, "the redirect is what puts stderr in the log; keep it"
    assert "$ErrorActionPreference = 'Continue'" in text, \
        "the redirect is only safe with the preference dropped for the invocation"


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
