"""Saying so when the report settings cannot produce a report.

Measured on the live installation on 2026-09-05: notifications were switched on
and "Email A Report After" was set to every run, yet no report had been produced
since 10 August. Nothing was broken. The emailed report is built at exactly one
place in the code, inside the Match and Assign action, and this installation's
schedule runs Sort Alternate Streams only. So the setting could never do
anything, and nothing said so.

Newsflasharr's own routing was fine, which made it worse: the existing readiness
check looked at delivery, found it healthy, and appended "Email delivery: reports
route to email". True, and misleading, because no report was ever going to be
handed to it.

This is the same failure shape as the two false lines found in the CSV export
preamble the same day. A setting that reads as switched on, a check that reads as
passing, and nothing happening.

WHAT COUNTS AS A GAP, and what deliberately does not:

  Report trigger "scheduled runs only" with a schedule that does not run Match
  and Assign: no report can EVER be sent. Always a gap.

  Report trigger "every run" with such a schedule: scheduled runs send nothing,
  but pressing Match and Assign by hand still sends. A gap worth naming, because
  the operator expects their nightly run to report.

  Report trigger "every run" with NO schedule at all: not a gap. Manual runs are
  the only runs, and they send.

  Notifications off, or the trigger set to never: never a gap. The operator is
  not asking for anything.

This is reported as a warning rather than an error. Running Sort on a schedule
and keeping notifications on for manual runs is a legitimate configuration, not a
fault, so it must not make Validate Settings report failure.
"""


def _plugin(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def _gap(plugin_module, **settings):
    base = {"notify_enabled": True, "notify_report_on": "every_run"}
    base.update(settings)
    return _plugin(plugin_module)._report_delivery_gap(base)


# --------------------------------------------------------------------------- #
# When there is a gap
# --------------------------------------------------------------------------- #
def test_scheduled_only_reports_with_no_scheduled_matching_can_never_send(plugin_module):
    gap = _gap(plugin_module, notify_report_on="scheduled",
               scheduled_match_streams=False, scheduled_times="0505")
    assert gap
    assert "match" in gap.lower()
    assert "no report" in gap.lower() or "never" in gap.lower()


def test_scheduled_only_reports_with_no_schedule_at_all_can_never_send(plugin_module):
    gap = _gap(plugin_module, notify_report_on="scheduled",
               scheduled_match_streams=False, scheduled_times="")
    assert gap


def test_every_run_with_a_sort_only_schedule_names_the_scheduled_runs(plugin_module):
    """The live case. Manual runs still send, so the wording must not overstate."""
    gap = _gap(plugin_module, notify_report_on="every_run",
               scheduled_match_streams=False, scheduled_times="0505")
    assert gap
    assert "schedul" in gap.lower()


def test_a_setting_stored_as_a_string_is_read_the_same_way(plugin_module):
    """Dispatcharr stores some booleans as the strings true and false."""
    gap = _gap(plugin_module, notify_report_on="scheduled",
               scheduled_match_streams="false", scheduled_times="0505")
    assert gap


# --------------------------------------------------------------------------- #
# When there is not
# --------------------------------------------------------------------------- #
def test_a_schedule_that_runs_matching_has_no_gap(plugin_module):
    assert _gap(plugin_module, notify_report_on="scheduled",
                scheduled_match_streams=True, scheduled_times="0505") is None


def test_every_run_with_no_schedule_has_no_gap(plugin_module):
    """Manual runs are the only runs, and a manual Match and Assign does report."""
    assert _gap(plugin_module, notify_report_on="every_run",
                scheduled_match_streams=False, scheduled_times="") is None


def test_notifications_switched_off_is_never_a_gap(plugin_module):
    assert _gap(plugin_module, notify_enabled=False,
                scheduled_match_streams=False, scheduled_times="0505") is None


def test_a_trigger_of_never_is_never_a_gap(plugin_module):
    assert _gap(plugin_module, notify_report_on="never",
                scheduled_match_streams=False, scheduled_times="0505") is None


# --------------------------------------------------------------------------- #
# The operator has to be able to SEE it
# --------------------------------------------------------------------------- #
def test_validate_settings_puts_a_warning_where_the_operator_reads_it(plugin_module,
                                                                     monkeypatch):
    """The full result list goes to the container log, which nobody watches.

    Only the returned message reaches the notification the operator actually
    sees, so a warning that stays in the list is barely more visible than the
    silence it replaced.
    """
    warning = chr(0x26A0) + " Email report: the schedule cannot produce one"
    monkeypatch.setattr(type(_plugin(plugin_module)), "_validate_plugin_settings",
                        lambda self, settings, logger: (False, ["ok", warning]),
                        raising=True)
    import logging
    result = _plugin(plugin_module).validate_settings_action({}, logging.getLogger("t"))

    assert result["status"] == "success"
    assert "schedule cannot produce one" in result["message"]


def test_a_warning_does_not_turn_a_passing_validation_into_a_failure(plugin_module,
                                                                    monkeypatch):
    """Sort on a schedule with notifications on for manual runs is a choice."""
    warning = chr(0x26A0) + " Email report: something worth knowing"
    monkeypatch.setattr(type(_plugin(plugin_module)), "_validate_plugin_settings",
                        lambda self, settings, logger: (False, [warning]),
                        raising=True)
    import logging
    result = _plugin(plugin_module).validate_settings_action({}, logging.getLogger("t"))
    assert result["status"] == "success"


def test_the_validate_message_still_fits_a_toast(plugin_module, monkeypatch):
    """A toast is clipped from the MIDDLE with no ellipsis, so it must fit."""
    warnings = [chr(0x26A0) + " Email report: " + ("a very long explanation " * 6)
                for _ in range(4)]
    monkeypatch.setattr(type(_plugin(plugin_module)), "_validate_plugin_settings",
                        lambda self, settings, logger: (False, warnings),
                        raising=True)
    import logging
    result = _plugin(plugin_module).validate_settings_action({}, logging.getLogger("t"))
    assert len(result["message"]) <= plugin_module.PluginConfig.TOAST_BUDGET_CHARS
