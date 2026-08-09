"""The Report a Bug button: what it writes, what it emails, and what it must
never carry.

MEASURED on a live installation 2026-08-09: the file this button writes carried
the provider's hostname three times and the M3U account suffixes three times, in
a file whose own opening lines tell the reader to paste it into a PUBLIC issue.
They arrived through the selected_m3us setting, which holds the M3U account
names, and on a real installation an account name is the provider hostname plus
a suffix. The masking list covered webhook_url and nothing else.

So the first three tests here are the real regression locks. The rest cover the
delivery decision.
"""
import json
import os

import pytest


ACCOUNT = "provider.tv-alt1"


def _plugin(plugin_module, tmp_path):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "1.0.0-test"
    inst.BUG_REPORT_DIR = str(tmp_path / "config")
    return inst


class _Logger:
    def _record(self, msg, *a, **k):
        pass
    info = debug = warning = error = _record


# --------------------------------------------------------------------------- #
# The account names must not reach the file OR the email
# --------------------------------------------------------------------------- #

def test_the_m3u_source_list_is_masked(plugin_module, tmp_path):
    inst = _plugin(plugin_module, tmp_path)
    out = inst._redact_settings_for_report(
        {"selected_m3us": ACCOUNT + ", " + ACCOUNT + "-two"})
    assert ACCOUNT not in json.dumps(out)


def test_masking_keeps_the_count_because_that_is_the_useful_part(plugin_module, tmp_path):
    """How many sources are configured is what a maintainer needs. Their names
    never are."""
    inst = _plugin(plugin_module, tmp_path)
    out = inst._redact_settings_for_report({"selected_m3us": "a, b, c"})
    assert "3" in out["selected_m3us"]


def test_a_list_valued_source_setting_is_also_masked(plugin_module, tmp_path):
    """Dispatcharr stores this as a string today. A list must not fall through
    the string branch and be emitted verbatim."""
    inst = _plugin(plugin_module, tmp_path)
    out = inst._redact_settings_for_report({"selected_m3us": [ACCOUNT, "second"]})
    assert ACCOUNT not in json.dumps(out)
    assert "2" in out["selected_m3us"]


def test_the_webhook_is_still_masked(plugin_module, tmp_path):
    """The pre-existing guarantee must survive the new one being added."""
    inst = _plugin(plugin_module, tmp_path)
    out = inst._redact_settings_for_report({"webhook_url": "https://hooks.example/T/B/XYZ"})
    assert "XYZ" not in json.dumps(out)


def test_an_unrelated_setting_is_left_alone(plugin_module, tmp_path):
    """Masking everything would make the report useless."""
    inst = _plugin(plugin_module, tmp_path)
    out = inst._redact_settings_for_report({"profile_name": "a", "match_sensitivity": "exact"})
    assert out["profile_name"] == "a"
    assert out["match_sensitivity"] == "exact"


# --------------------------------------------------------------------------- #
# The sanitised CSV
# --------------------------------------------------------------------------- #

def test_no_csv_is_attached_when_the_account_names_cannot_be_read(plugin_module, tmp_path):
    """FAIL CLOSED. Without the account names the sanitiser cannot remove them,
    and attaching the export unsanitised is the exact outcome this prevents."""
    inst = _plugin(plugin_module, tmp_path)
    exports = tmp_path / "exports"
    exports.mkdir()
    (exports / "e.csv").write_text("name\nBBC One [%s]\n" % ACCOUNT, encoding="utf-8")
    plugin_module.PluginConfig.EXPORTS_DIR = str(exports)

    def _boom(logger):
        raise RuntimeError("no database here")

    inst._get_all_m3u_accounts = _boom
    assert inst._build_sanitised_bug_csv(_Logger()) is None


def test_no_csv_when_there_are_no_exports(plugin_module, tmp_path):
    inst = _plugin(plugin_module, tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    plugin_module.PluginConfig.EXPORTS_DIR = str(empty)
    inst._get_all_m3u_accounts = lambda logger: [{"name": ACCOUNT}]
    assert inst._build_sanitised_bug_csv(_Logger()) is None


def test_the_account_name_is_removed_from_the_copied_csv(plugin_module, tmp_path):
    inst = _plugin(plugin_module, tmp_path)
    exports = tmp_path / "exports2"
    exports.mkdir()
    (exports / "e.csv").write_text(
        "# Selected M3U Sources: %s\nname\nBBC One [%s]\n" % (ACCOUNT, ACCOUNT),
        encoding="utf-8")
    plugin_module.PluginConfig.EXPORTS_DIR = str(exports)
    inst._get_all_m3u_accounts = lambda logger: [{"name": ACCOUNT}]

    out = inst._build_sanitised_bug_csv(_Logger())
    assert out is not None
    text = open(out, encoding="utf-8").read()
    assert ACCOUNT not in text
    # The preamble names the sources too, not just the stream column.
    assert "BBC One" in text


def test_the_original_export_is_not_modified(plugin_module, tmp_path):
    """It is a COPY. Rewriting the operator's export in place would be a
    surprise, and other tooling reads those files."""
    inst = _plugin(plugin_module, tmp_path)
    exports = tmp_path / "exports3"
    exports.mkdir()
    src = exports / "e.csv"
    original = "name\nBBC One [%s]\n" % ACCOUNT
    src.write_text(original, encoding="utf-8")
    plugin_module.PluginConfig.EXPORTS_DIR = str(exports)
    inst._get_all_m3u_accounts = lambda logger: [{"name": ACCOUNT}]
    inst._build_sanitised_bug_csv(_Logger())
    assert src.read_text(encoding="utf-8") == original


# --------------------------------------------------------------------------- #
# Whether it emails at all
# --------------------------------------------------------------------------- #

def _bridge_stub(enabled, trigger):
    class _B:
        SOURCE = "stream-mapparr"
        EVENT = "usage_report"

        @staticmethod
        def is_enabled(settings):
            return enabled

        @staticmethod
        def resolve_report_trigger(settings):
            return trigger
    return _B


def test_nothing_is_emailed_when_newsflasharr_is_off(plugin_module, tmp_path):
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(False, "every_run")
    sent, reason = inst._email_bug_report({}, _Logger(), "text", None)
    assert sent is False
    assert "switched off" in reason


def test_nothing_is_emailed_when_the_trigger_is_never(plugin_module, tmp_path):
    """The operator chose to have the button respect that setting."""
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(True, "never")
    sent, reason = inst._email_bug_report({}, _Logger(), "text", None)
    assert sent is False
    assert "never" in reason


def test_it_emails_on_scheduled_because_a_button_press_is_not_a_run(plugin_module, tmp_path):
    """"scheduled" limits the periodic REPORT. A bug report is neither scheduled
    nor a run, and refusing to send one here would be surprising."""
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(True, "scheduled")
    calls = []

    class _Client:
        @staticmethod
        def notify(**kw):
            calls.append(kw)
            return True

    inst._notify_client = lambda: _Client
    sent, reason = inst._email_bug_report({}, _Logger(), "the report text", None)
    assert sent is True
    assert reason is None
    assert len(calls) == 1


def test_the_report_text_travels_in_the_body_not_as_an_attachment(plugin_module, tmp_path):
    """Newsflasharr accepts only .html, .htm and .csv attachments, so a .txt bug
    report cannot be attached at all. The body cap is 64 KB."""
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(True, "every_run")
    calls = []

    class _Client:
        @staticmethod
        def notify(**kw):
            calls.append(kw)
            return True

    inst._notify_client = lambda: _Client
    inst._email_bug_report({}, _Logger(), "UNIQUE-REPORT-BODY", None)
    assert "UNIQUE-REPORT-BODY" in calls[0]["body"]
    assert "attachment" not in calls[0]


def test_the_single_attachment_slot_carries_the_csv(plugin_module, tmp_path):
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(True, "every_run")
    csv_file = tmp_path / "data.csv"
    csv_file.write_text("a,b\n", encoding="utf-8")
    calls = []

    class _Client:
        @staticmethod
        def notify(**kw):
            calls.append(kw)
            return True

    inst._notify_client = lambda: _Client
    inst._email_bug_report({}, _Logger(), "text", str(csv_file))
    assert calls[0]["attachment"] == str(csv_file)


def test_a_missing_csv_path_is_not_attached(plugin_module, tmp_path):
    """A path that does not exist must not be sent: an SMTP retry re-reads it."""
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(True, "every_run")
    calls = []

    class _Client:
        @staticmethod
        def notify(**kw):
            calls.append(kw)
            return True

    inst._notify_client = lambda: _Client
    inst._email_bug_report({}, _Logger(), "text", str(tmp_path / "gone.csv"))
    assert "attachment" not in calls[0]


def test_a_raising_client_is_contained(plugin_module, tmp_path):
    """Failing to email must never stop the file being written, which is the
    fallback the operator always has."""
    inst = _plugin(plugin_module, tmp_path)
    inst._notify_bridge = lambda: _bridge_stub(True, "every_run")

    class _Client:
        @staticmethod
        def notify(**kw):
            raise RuntimeError("spool exploded")

    inst._notify_client = lambda: _Client
    sent, reason = inst._email_bug_report({}, _Logger(), "text", None)
    assert sent is False
    assert "contained" in reason
