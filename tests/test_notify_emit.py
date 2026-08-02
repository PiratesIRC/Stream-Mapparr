"""The emit layer: which runs send a report, and the scheduled-run stamp.

Both directions of every gate are proved. A test that only checks the off case
passes against an emit path that is dead in both directions.
"""


class _Spy:
    """Records notify() calls without needing a spool directory."""

    def __init__(self, result=True):
        self.calls = []
        self.result = result

    def __call__(self, source, title, **kw):
        self.calls.append({"source": source, "title": title, **kw})
        return self.result


def _written(tmp_path):
    h = tmp_path / "stream_mapparr_report_20260801_120000.html"
    c = tmp_path / "stream_mapparr_report_20260801_120000.csv"
    h.write_text("<html></html>", encoding="utf-8")
    c.write_text("a,b\n1,2\n", encoding="utf-8")
    return {"html_path": str(h), "csv_path": str(c), "error": None}


# --------------------------------------------------------------------------- #
# Which runs emit
# --------------------------------------------------------------------------- #

def test_master_toggle_off_emits_nothing(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": False, "notify_report_on": "every_run"},
                       _written(tmp_path), is_scheduled=True)
    assert out["sent"] == 0
    assert spy.calls == []


def test_trigger_never_emits_nothing_even_with_the_master_toggle_on(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "never"},
                       _written(tmp_path), is_scheduled=True)
    assert out["sent"] == 0
    assert spy.calls == []


def test_trigger_scheduled_emits_on_a_scheduled_run(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "scheduled"},
                       _written(tmp_path), is_scheduled=True)
    assert out["sent"] == 2


def test_trigger_scheduled_emits_nothing_on_a_manual_run(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "scheduled"},
                       _written(tmp_path), is_scheduled=False)
    assert out["sent"] == 0
    assert spy.calls == []


def test_trigger_every_run_emits_on_a_manual_run(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run"},
                       _written(tmp_path), is_scheduled=False)
    assert out["sent"] == 2


# --------------------------------------------------------------------------- #
# What each event looks like
# --------------------------------------------------------------------------- #

def test_both_files_are_sent_as_separate_events(tmp_path):
    """A notification carries one attachment, so two files means two events."""
    from notify_bridge import emit_reports
    spy = _Spy()
    w = _written(tmp_path)
    emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run"},
                 w, is_scheduled=False)
    assert sorted(c["attachment"] for c in spy.calls) == sorted(
        [w["html_path"], w["csv_path"]])


def test_every_event_uses_the_report_conventions(tmp_path):
    """A report is not an incident: no dedup_key, severity info, and the source
    is the plugin key that Newsflasharr routing is keyed on."""
    from notify_bridge import emit_reports
    spy = _Spy()
    emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run"},
                 _written(tmp_path), is_scheduled=False)
    assert len(spy.calls) == 2
    for call in spy.calls:
        assert call["source"] == "stream-mapparr"
        assert call["event"] == "usage_report"
        assert call["severity"] == "info"
        assert call["dedup_key"] is None


# --------------------------------------------------------------------------- #
# Failure handling
# --------------------------------------------------------------------------- #

def test_a_missing_artifact_is_not_emitted(tmp_path):
    """Emit only after a CONFIRMED publish. A green task result does not prove
    the artifact exists on disk."""
    import os

    from notify_bridge import emit_reports
    spy = _Spy()
    w = _written(tmp_path)
    os.unlink(w["csv_path"])
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run"},
                       w, is_scheduled=False)
    assert out["sent"] == 1


def test_a_write_failure_emits_nothing(tmp_path):
    """If the report writer reported an error there is nothing to announce."""
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run"},
                       {"html_path": None, "csv_path": None, "error": "disk full"},
                       is_scheduled=False)
    assert out["sent"] == 0
    assert spy.calls == []


def test_a_raising_notify_function_is_contained(tmp_path):
    """The isolation invariant: a bug in the emit path must never break the
    plugin's real work."""
    from notify_bridge import emit_reports

    def boom(*a, **k):
        raise RuntimeError("spool exploded")

    out = emit_reports(boom, {"notify_enabled": True, "notify_report_on": "every_run"},
                       _written(tmp_path), is_scheduled=False)
    assert out["sent"] == 0
    assert out["skipped_reason"]


def test_a_refused_spool_write_is_counted_as_not_sent(tmp_path):
    """notify() returns False rather than raising when the spool refuses."""
    from notify_bridge import emit_reports
    spy = _Spy(result=False)
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run"},
                       _written(tmp_path), is_scheduled=False)
    assert out["sent"] == 0
    assert len(spy.calls) == 2


# --------------------------------------------------------------------------- #
# Resolving the settings
# --------------------------------------------------------------------------- #

def test_an_unknown_trigger_value_falls_back_to_scheduled():
    from notify_bridge import resolve_report_trigger
    assert resolve_report_trigger({"notify_report_on": "banana"}) == "scheduled"
    assert resolve_report_trigger({"notify_report_on": None}) == "scheduled"
    assert resolve_report_trigger({}) == "scheduled"


def test_the_trigger_tolerates_case_and_whitespace():
    from notify_bridge import resolve_report_trigger
    assert resolve_report_trigger({"notify_report_on": "  Every_Run "}) == "every_run"


def test_a_string_master_toggle_is_coerced(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": "true", "notify_report_on": "every_run"},
                       _written(tmp_path), is_scheduled=False)
    assert out["sent"] == 2


def test_is_enabled_is_public_and_coerces_a_string():
    """Public on purpose: the Email Report Now button needs this check before it
    does any work, and a caller in another module must not reach for a private
    helper that nothing pins."""
    from notify_bridge import is_enabled
    assert is_enabled({"notify_enabled": True}) is True
    assert is_enabled({"notify_enabled": "yes"}) is True
    assert is_enabled({"notify_enabled": "false"}) is False
    assert is_enabled({}) is False


# --------------------------------------------------------------------------- #
# The scheduled-run stamp
# --------------------------------------------------------------------------- #

def test_the_stamp_round_trips(tmp_path):
    from notify_bridge import read_scheduled_run_ts, write_scheduled_run_ts
    p = str(tmp_path / "sched.json")
    write_scheduled_run_ts(p, 1785237435.5)
    assert read_scheduled_run_ts(p) == 1785237435.5


def test_a_missing_stamp_reads_as_never_ran(tmp_path):
    from notify_bridge import read_scheduled_run_ts
    assert read_scheduled_run_ts(str(tmp_path / "absent.json")) is None


def test_a_corrupt_stamp_reads_as_never_ran(tmp_path):
    """Degrade toward "never ran", which is the safe direction: an unreadable
    health signal must not be mistaken for a healthy one."""
    from notify_bridge import read_scheduled_run_ts
    p = tmp_path / "sched.json"
    p.write_text("{not json", encoding="utf-8")
    assert read_scheduled_run_ts(str(p)) is None


def test_writing_the_stamp_never_raises_on_an_unwritable_path(tmp_path):
    """A health signal must not break the run it reports on."""
    from notify_bridge import write_scheduled_run_ts
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("x", encoding="utf-8")
    write_scheduled_run_ts(str(blocker / "nested" / "sched.json"), 1.0)


def test_the_stamp_write_is_atomic_and_leaves_no_temporary(tmp_path):
    import json
    import os

    from notify_bridge import write_scheduled_run_ts
    p = tmp_path / "sched.json"
    write_scheduled_run_ts(str(p), 1.0)
    write_scheduled_run_ts(str(p), 2.0)
    assert json.loads(p.read_text(encoding="utf-8"))["last_scheduled_run_ts"] == 2.0
    assert [f for f in os.listdir(tmp_path) if ".tmp" in f] == []


# --------------------------------------------------------------------------- #
# Which file formats are emailed
# --------------------------------------------------------------------------- #

def test_the_format_defaults_to_both():
    from notify_bridge import resolve_report_format
    assert resolve_report_format({}) == "both"


def test_an_unknown_format_falls_back_to_both():
    """Falling back to both is the safe direction: the operator still receives
    the report rather than silently receiving nothing."""
    from notify_bridge import resolve_report_format
    assert resolve_report_format({"notify_report_format": "pdf"}) == "both"
    assert resolve_report_format({"notify_report_format": None}) == "both"


def test_the_format_tolerates_case_and_whitespace():
    from notify_bridge import resolve_report_format
    assert resolve_report_format({"notify_report_format": "  HTML "}) == "html"


def test_html_only_sends_one_email(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    w = _written(tmp_path)
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run",
                             "notify_report_format": "html"}, w, is_scheduled=False)
    assert out["sent"] == 1
    assert spy.calls[0]["attachment"] == w["html_path"]


def test_csv_only_sends_one_email(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    w = _written(tmp_path)
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run",
                             "notify_report_format": "csv"}, w, is_scheduled=False)
    assert out["sent"] == 1
    assert spy.calls[0]["attachment"] == w["csv_path"]


def test_both_sends_two_emails(tmp_path):
    from notify_bridge import emit_reports
    spy = _Spy()
    w = _written(tmp_path)
    out = emit_reports(spy, {"notify_enabled": True, "notify_report_on": "every_run",
                             "notify_report_format": "both"}, w, is_scheduled=False)
    assert out["sent"] == 2
    assert sorted(c["attachment"] for c in spy.calls) == sorted(
        [w["html_path"], w["csv_path"]])


def test_the_format_does_not_override_the_other_gates(tmp_path):
    """Choosing a format is not a way to switch notifications on."""
    from notify_bridge import emit_reports
    spy = _Spy()
    out = emit_reports(spy, {"notify_enabled": False, "notify_report_on": "every_run",
                             "notify_report_format": "html"},
                       _written(tmp_path), is_scheduled=False)
    assert out["sent"] == 0
    assert spy.calls == []
