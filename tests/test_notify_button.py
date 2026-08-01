"""The Email Report Now button.

It refuses up front when the mail could not arrive, then does the work in a
background thread, because building a report costs a full matching pass and the
plugin runs under gevent where an in-request match freezes the whole worker.
"""
import inspect


class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def _actions(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    return inst.actions


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def test_the_action_is_registered_in_plugin_py(plugin_module):
    """Manifest actions are ignored for an enabled plugin, so a manifest-only
    entry renders no button at all."""
    assert "email_report_now" in [a["id"] for a in _actions(plugin_module)]


def test_the_action_carries_the_keys_the_card_needs(plugin_module):
    """Dispatcharr's action normaliser silently drops an action with no label."""
    action = next(a for a in _actions(plugin_module) if a["id"] == "email_report_now")
    assert action.get("label")
    assert action.get("button_label")
    assert action.get("button_color")


def test_the_action_is_in_the_manifest_too(plugin_module):
    """plugin.json and the plugin.py actions list must not drift."""
    import json
    import pathlib
    manifest = json.loads(
        (pathlib.Path(__file__).resolve().parent.parent /
         "Stream-Mapparr" / "plugin.json").read_text(encoding="utf-8"))
    assert "email_report_now" in [a["id"] for a in manifest.get("actions", [])]


def test_the_action_is_dispatchable(plugin_module):
    """An action with no entry in run()'s dispatch map is a dead button."""
    src = inspect.getsource(plugin_module.Plugin.run)
    assert "email_report_now" in src


def test_the_description_says_it_does_not_prove_the_schedule_works(plugin_module):
    """It runs in the web worker from the settings on screen; the schedule runs
    on a background worker from stored settings."""
    action = next(a for a in _actions(plugin_module) if a["id"] == "email_report_now")
    assert "schedule" in (action.get("description") or "").lower()


def test_no_em_dashes_in_the_action_copy(plugin_module):
    action = next(a for a in _actions(plugin_module) if a["id"] == "email_report_now")
    assert "—" not in (action.get("description") or "") + action.get("label", "")


# --------------------------------------------------------------------------- #
# The handler
# --------------------------------------------------------------------------- #

def test_the_button_never_writes_the_scheduled_run_stamp(plugin_module):
    """Only the schedule may set that signal. A button able to set it would
    mask a dead schedule indefinitely, because Newsflasharr's own absence
    detector cannot tell a button press from a scheduled run."""
    src = inspect.getsource(plugin_module.Plugin.email_report_now_action)
    assert "write_scheduled_run_ts" not in src


def test_the_button_checks_readiness_before_doing_the_work(plugin_module):
    """Building a report costs a full matching pass. Refusing first is the
    difference between a useful error and a green message about mail nobody
    receives."""
    src = inspect.getsource(plugin_module.Plugin.email_report_now_action)
    check_at = src.index("_newsflasharr_readiness")
    work_at = src.index("Thread")
    assert check_at < work_at


def test_notifications_off_refuses_with_an_error_key(plugin_module):
    """status renders nowhere on the plugin card, so a failure that sets only
    status is pixel-identical to success."""
    result = _bare(plugin_module).email_report_now_action(
        {"notify_enabled": False}, _Logger())
    assert result.get("error")
    assert result.get("status") == "error"


def test_a_readiness_problem_refuses_and_repeats_the_reason(plugin_module, monkeypatch):
    inst = _bare(plugin_module)
    monkeypatch.setattr(inst, "_newsflasharr_readiness",
                        lambda: ["Newsflasharr is installed but not enabled."])
    result = inst.email_report_now_action({"notify_enabled": True}, _Logger())
    assert result.get("error")
    assert "not enabled" in result["error"]


def test_a_ready_press_starts_the_work_and_says_so(plugin_module, monkeypatch):
    inst = _bare(plugin_module)
    monkeypatch.setattr(inst, "_newsflasharr_readiness", lambda: [])
    started = {}

    class _FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            started["target"] = target

        def start(self):
            started["started"] = True

    monkeypatch.setattr(plugin_module.threading, "Thread", _FakeThread)
    result = inst.email_report_now_action({"notify_enabled": True}, _Logger())
    assert result.get("status") == "success"
    assert started.get("started") is True
    assert result.get("error") is None


def test_the_message_does_not_claim_the_mail_was_sent(plugin_module, monkeypatch):
    """notify() returning True means durably queued, not delivered. Even an
    SMTP acceptance is acceptance for relay, not delivery."""
    inst = _bare(plugin_module)
    monkeypatch.setattr(inst, "_newsflasharr_readiness", lambda: [])

    class _FakeThread:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

    monkeypatch.setattr(plugin_module.threading, "Thread", _FakeThread)
    message = inst.email_report_now_action(
        {"notify_enabled": True}, _Logger()).get("message", "").lower()
    assert "sent" not in message
    assert "delivered" not in message


def test_the_background_work_forces_a_dry_run(plugin_module, monkeypatch):
    """The button must never change stream assignments. It reports on the
    current state, it does not act on it."""
    inst = _bare(plugin_module)
    monkeypatch.setattr(inst, "_newsflasharr_readiness", lambda: [])
    captured = {}

    def _fake_match(settings, logger, **kw):
        captured["settings"] = settings
        return {"status": "success"}

    monkeypatch.setattr(inst, "add_streams_to_channels_action", _fake_match)
    monkeypatch.setattr(inst, "_acquire_operation_lock", lambda *a, **k: True)
    monkeypatch.setattr(inst, "_release_operation_lock", lambda *a, **k: None)

    class _RunNow:
        def __init__(self, target=None, name=None, daemon=None):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(plugin_module.threading, "Thread", _RunNow)
    inst.email_report_now_action({"notify_enabled": True}, _Logger())

    assert captured["settings"]["dry_run_mode"] is True
    assert captured["settings"]["notify_report_on"] == "every_run"


def test_the_handler_never_raises(plugin_module, monkeypatch):
    inst = _bare(plugin_module)

    def _boom():
        raise RuntimeError("readiness exploded")

    monkeypatch.setattr(inst, "_newsflasharr_readiness", _boom)
    result = inst.email_report_now_action({"notify_enabled": True}, _Logger())
    assert result.get("error")


# --------------------------------------------------------------------------- #
# Why the export cleaner needs no age guard
# --------------------------------------------------------------------------- #

def test_the_export_cleaner_cannot_reach_an_emailed_report(plugin_module):
    """Newsflasharr re-reads an attachment path on every delivery retry across
    about 35 minutes, so deleting a report inside that window would strip the
    file from mail already queued.

    Clear CSV Exports deletes from /data/exports. The emailed reports are
    written to /data/stream_mapparr_reports. The two are disjoint, so no age
    guard is needed in the cleaner and none was added: a guard that cannot fire
    reads as protection while providing none.

    This test exists so that stays true. Moving the reports into the exports
    directory, or pointing the cleaner at the reports directory, must fail here
    rather than silently reintroduce the problem.
    """
    reports = plugin_module.Plugin._reports()
    exports_dir = plugin_module.PluginConfig.EXPORTS_DIR.rstrip("/")
    report_dir = reports.REPORT_DIR.rstrip("/")
    assert report_dir != exports_dir
    assert not report_dir.startswith(exports_dir + "/")
    assert not exports_dir.startswith(report_dir + "/")
