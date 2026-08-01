"""Wiring the report into the scheduler and the run paths.

These are mostly source-level checks. The alternative, driving the real
scheduler loop, would need a live Django ORM and a wall-clock wait, and would
still not prove the stamp is in the right BRANCH, which is the thing that was
wrong in an earlier design.
"""
import inspect


def test_the_scheduler_stamps_the_run_not_the_match_substep(plugin_module):
    """scheduled_sort_streams and scheduled_match_streams are independent
    toggles. An operator who schedules Sort only has a healthy schedule, and it
    must not report "never recorded" forever.

    The scheduler is a nested function named scheduler_loop defined inside
    _start_background_scheduler_locked, not a method, so reading the enclosing
    method is the only way to see it.
    """
    loop = inspect.getsource(plugin_module.Plugin._start_background_scheduler_locked)
    assert "write_scheduled_run_ts" in loop, (
        "the scheduler loop must record that a scheduled run completed"
    )
    match_action = inspect.getsource(plugin_module.Plugin.add_streams_to_channels_action)
    assert "write_scheduled_run_ts" not in match_action, (
        "the stamp must not live inside one sub-step: a Sort-only schedule would "
        "never record a run"
    )


def test_the_report_is_actually_built_and_emitted(plugin_module):
    """An earlier draft created the report module and never called it, so the
    feature was unit-tested in isolation and never produced by a real run."""
    src = inspect.getsource(plugin_module.Plugin._build_and_emit_reports)
    assert "write_report" in src
    assert "emit_reports" in src


def test_the_match_action_builds_report_input_and_emits(plugin_module):
    src = inspect.getsource(plugin_module.Plugin.add_streams_to_channels_action)
    assert "_build_and_emit_reports" in src


def test_report_input_is_not_taken_from_the_csv_export_variable(plugin_module):
    """csv_data is built only inside `if dry_run or create_csv:`, so on an
    ordinary live run with CSV export off the name does not exist. Its
    stream_names is also one joined string, which the report model rejects."""
    src = inspect.getsource(plugin_module.Plugin.add_streams_to_channels_action)
    emit_call = src.split("_build_and_emit_reports")[1][:400]
    assert "csv_data" not in emit_call


def test_the_helper_modules_are_imported_lazily(plugin_module):
    """A module-scope import would break Dispatcharr's loader the same way a
    top-level Django import does."""
    for name in ("_notify_client", "_notify_bridge", "_reports"):
        src = inspect.getsource(getattr(plugin_module.Plugin, name))
        assert "import" in src
        assert "try:" in src, f"{name} must fall back when the relative import fails"


def test_validate_settings_surfaces_the_scheduled_run_age(plugin_module):
    """The operator-readable answer to "is the schedule actually running".
    Newsflasharr's own absence detector cannot answer it: that timestamp is set
    by any attachment send, including a button press."""
    src = inspect.getsource(plugin_module.Plugin._validate_plugin_settings)
    assert "read_scheduled_run_ts" in src


def test_validate_settings_reports_an_unknown_trigger_value(plugin_module):
    """A caller-side filter setting's unknown-value case must reach a surface
    the operator actually reads. A promise in help text is not a surface."""
    src = inspect.getsource(plugin_module.Plugin._validate_plugin_settings)
    assert "resolve_report_trigger" in src


def test_build_and_emit_reports_never_raises(plugin_module, monkeypatch):
    """Reporting is not the plugin's real work. A failure anywhere in it must be
    returned, not thrown into the run that produced the data."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("module exploded")

    monkeypatch.setattr(inst, "_reports", lambda: _Boom())

    class _Logger:
        def _record(self, msg, *a, **k):
            pass
        info = debug = warning = error = _record

    out = inst._build_and_emit_reports({}, _Logger(), [], [], is_scheduled=True)
    assert out["sent"] == 0
    assert out["skipped_reason"]


def test_build_and_emit_produces_real_files_and_two_events(plugin_module, tmp_path, monkeypatch):
    """End to end through the helper: real rendering, real files on disk, two
    notifications, and no M3U account name anywhere in either file."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    reports = inst._reports()
    monkeypatch.setattr(reports, "REPORT_DIR", str(tmp_path))

    calls = []

    class _FakeClient:
        @staticmethod
        def notify(source, title, **kw):
            calls.append(kw)
            return True

    monkeypatch.setattr(inst, "_notify_client", lambda: _FakeClient)

    class _Logger:
        def _record(self, msg, *a, **k):
            pass
        info = debug = warning = error = _record

    out = inst._build_and_emit_reports(
        {"notify_enabled": True, "notify_report_on": "every_run"},
        _Logger(),
        [{"channel_name": "Sky News",
          "stream_names": ["SKY NEWS HD", "US: ABC 45 HD [WINSTON-SALEM]"]}],
        ["streamq.tv", "streamq.tv-bk15"],
        is_scheduled=False)

    assert out["sent"] == 2, out
    # Only the report files: the test harness also redirects the plugin's /data
    # paths into this same temporary directory.
    produced = sorted(p for p in tmp_path.iterdir()
                      if p.name.startswith("stream_mapparr_report_"))
    assert len(produced) == 2
    assert any(p.name.endswith(".html") for p in produced)
    assert any(p.name.endswith(".csv") for p in produced)

    for path in produced:
        text = path.read_text(encoding="utf-8")
        assert "streamq" not in text, f"provider hostname leaked into {path.name}"
        assert "[WINSTON-SALEM]" in text, "the market label must survive"


def test_build_and_emit_writes_nothing_when_the_toggle_is_off(plugin_module, tmp_path, monkeypatch):
    """Do not pay for building a report nobody will receive."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    monkeypatch.setattr(inst._reports(), "REPORT_DIR", str(tmp_path))

    class _Logger:
        def _record(self, msg, *a, **k):
            pass
        info = debug = warning = error = _record

    out = inst._build_and_emit_reports(
        {"notify_enabled": False}, _Logger(),
        [{"channel_name": "Sky News", "stream_names": ["x"]}], [], is_scheduled=True)
    assert out["sent"] == 0
    produced = [p for p in tmp_path.iterdir()
                if p.name.startswith("stream_mapparr_report_")]
    assert produced == [], "no report file may be written when notifications are off"
