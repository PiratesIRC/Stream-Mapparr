"""The runtime estimate, and the message a run reports when it added nothing.

Both came from one real log line. A run over 117 channel groups and 19,493
streams announced "estimated ~1h 1m" and finished in 18.2 seconds, a 202-fold
over-estimate, and then reported "Matched and assigned 0 streams across 103
channels", which reads as a failure and was not one.

THE ESTIMATE COULD NOT CORRECT ITSELF. Every run writes its real cost to a
timing file so the next estimate uses the machine's own speed. That
installation recorded 0.008 seconds per group per 1000 streams, correctly. The
reader rejected it, because the accepted range started at 0.01 and the machine
was simply faster than that. The shipped fallback of 1.61 was used on every run
instead, forever.
"""
import json


def _inst(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst


def _write_rate(tmp_path, rate):
    path = tmp_path / "timing.json"
    path.write_text(json.dumps({
        "groups": 117, "streams": 19493, "duration_seconds": 18.2,
        "seconds_per_group_per_1k_streams": rate}), encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# The rate a real installation measured must be believed
# --------------------------------------------------------------------------- #

def test_the_rate_measured_on_a_live_installation_is_accepted(plugin_module, tmp_path):
    """0.008 is a REAL reading: 117 groups over 19,493 streams in 18.2 seconds.
    It was rejected, which is the whole defect."""
    inst = _inst(plugin_module)
    assert inst._observed_rate(_write_rate(tmp_path, 0.008)) == 0.008


def test_a_rate_at_the_floor_is_accepted(plugin_module, tmp_path):
    inst = _inst(plugin_module)
    floor = plugin_module.PluginConfig.MEASURED_MIN_RATE
    assert inst._observed_rate(_write_rate(tmp_path, floor)) == floor


def test_zero_is_still_rejected(plugin_module, tmp_path):
    """A zero rate predicts an instantaneous run for any job at all."""
    inst = _inst(plugin_module)
    assert inst._observed_rate(_write_rate(tmp_path, 0.0)) is None


def test_a_negative_rate_is_rejected(plugin_module, tmp_path):
    inst = _inst(plugin_module)
    assert inst._observed_rate(_write_rate(tmp_path, -1.0)) is None


def test_not_a_number_is_rejected(plugin_module, tmp_path):
    """NaN passes every comparison it is given, so a bare range check lets it
    through and it then poisons the arithmetic downstream."""
    inst = _inst(plugin_module)
    path = tmp_path / "nan.json"
    path.write_text('{"seconds_per_group_per_1k_streams": NaN}', encoding="utf-8")
    assert inst._observed_rate(str(path)) is None


def test_an_absurdly_slow_rate_is_rejected(plugin_module, tmp_path):
    """A run that sat behind a lock must not poison later estimates."""
    inst = _inst(plugin_module)
    assert inst._observed_rate(_write_rate(tmp_path, 5000.0)) is None


def test_a_missing_file_falls_back_rather_than_raising(plugin_module, tmp_path):
    inst = _inst(plugin_module)
    assert inst._observed_rate(str(tmp_path / "absent.json")) is None


# --------------------------------------------------------------------------- #
# The floor must not be low enough to trigger synchronous dispatch
# --------------------------------------------------------------------------- #

def test_the_floor_still_keeps_a_real_job_off_the_synchronous_path(plugin_module):
    """A CPU-bound matching loop on the synchronous path freezes the whole uWSGI
    worker, not one request. A rate small enough to predict under the sync
    threshold would send a real job there, so the floor is load-bearing."""
    cfg = plugin_module.PluginConfig
    seconds = plugin_module.estimate_run_seconds(117, 19493, rate=cfg.MEASURED_MIN_RATE)
    assert cfg.ETA_SAFETY_FACTOR * seconds >= cfg.SYNC_THRESHOLD_SECONDS, (
        "at the floor a 117-group run estimates %.2fs, which dispatches "
        "synchronously" % seconds)


def test_the_floor_leaves_room_below_the_measured_rate(plugin_module):
    """If the floor sat just under the real reading, a slightly faster machine
    would hit the same wall this fixed."""
    assert plugin_module.PluginConfig.MEASURED_MIN_RATE <= 0.008 / 4


def test_the_estimate_uses_the_measured_rate_when_one_exists(plugin_module, tmp_path):
    """End to end: the number an operator sees comes from the file, not the
    shipped constant."""
    inst = _inst(plugin_module)
    rate = inst._observed_rate(_write_rate(tmp_path, 0.008))
    measured = plugin_module.estimate_run_seconds(117, 19493, rate=rate)
    shipped = plugin_module.estimate_run_seconds(117, 19493)
    assert 15 <= measured <= 22, measured          # the run really took 18.2 s
    assert shipped > 3000                          # the fallback says over an hour


# --------------------------------------------------------------------------- #
# The completion message when a run matched but wrote nothing
# --------------------------------------------------------------------------- #

def _message_for(plugin_module, added, updated):
    """The message-building branch, exercised through the real source so the
    wording cannot drift away from what the action actually emits."""
    import re
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    block = src[src.index("if dry_run:\n                success_msg ="):]
    block = block[:block.index("if channels_skipped > 0")]
    assert "Nothing new to add" in block, "the no-op branch is gone"
    return block


def test_a_run_that_added_nothing_does_not_report_zero_as_the_headline(plugin_module):
    """"Matched and assigned 0 streams" reads as a failure. It is what a second
    run over an unchanged library correctly produces when Overwrite Existing
    Streams is off, and it sent an operator looking for a fault."""
    block = _message_for(plugin_module, 0, 103)
    assert "total_streams_added == 0 and channels_updated > 0" in block


def test_the_no_op_message_names_the_setting_that_would_change_it(plugin_module):
    """Telling the operator nothing was added is only half an answer."""
    block = _message_for(plugin_module, 0, 103)
    assert "Overwrite Existing Streams" in block


def test_a_run_that_added_streams_still_reports_the_count(plugin_module):
    block = _message_for(plugin_module, 742, 103)
    assert "Matched and assigned {total_streams_added} streams" in block


def test_a_dry_run_message_is_unchanged(plugin_module):
    block = _message_for(plugin_module, 0, 103)
    assert "Dry run complete." in block


# --------------------------------------------------------------------------- #
# The dispatch decision must not inherit a cheap run's rate
# --------------------------------------------------------------------------- #

def test_the_dispatch_rate_never_undercuts_the_shipped_fallback(plugin_module):
    """MEASURED on one machine: 0.008 seconds per group per 1000 streams against
    the 1,899-entry UK channel database, and 0.4849 against the 31,823-entry US
    one. Sixty times apart, same box, and the model has no term for the database
    size, so the stored rate is whatever ran last.

    This number decides SYNCHRONOUS versus background dispatch, and a CPU-bound
    matching loop on the synchronous path freezes the entire uWSGI worker rather
    than one request. Carrying a cheap run's rate into an expensive one would
    send it inline.
    """
    P = plugin_module.Plugin
    cfg = plugin_module.PluginConfig
    assert P._dispatch_rate(0.008) == cfg.ESTIMATED_SECONDS_PER_GROUP_PER_1K_STREAMS


def test_a_slower_measured_rate_is_kept(plugin_module):
    """Clamping upward only. A machine genuinely slower than the shipped guess
    must keep its own number, or the estimate under-reports again."""
    P = plugin_module.Plugin
    assert P._dispatch_rate(5.0) == 5.0


def test_no_measurement_means_use_the_shipped_fallback(plugin_module):
    P = plugin_module.Plugin
    assert P._dispatch_rate(None) is None


def test_the_clamp_actually_prevents_synchronous_dispatch(plugin_module):
    """The property that matters, stated in seconds rather than in rates: a
    seven-group run over 19,493 streams would dispatch inline on the cheap rate
    and must not on the clamped one."""
    P = plugin_module.Plugin
    cfg = plugin_module.PluginConfig
    optimistic = plugin_module.estimate_run_seconds(7, 19493, rate=0.008)
    clamped = plugin_module.estimate_run_seconds(
        7, 19493, rate=P._dispatch_rate(0.008))
    assert cfg.ETA_SAFETY_FACTOR * optimistic < cfg.SYNC_THRESHOLD_SECONDS, (
        "premise gone: the cheap rate would not dispatch synchronously anyway")
    assert cfg.ETA_SAFETY_FACTOR * clamped >= cfg.SYNC_THRESHOLD_SECONDS


def test_the_dispatch_path_uses_the_clamp(plugin_module):
    """Source-level, because the surrounding method needs more instance state
    than this harness can supply. It proves the call site exists, not that it
    runs."""
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    block = src[src.index("def _estimate_eta_seconds"):]
    block = block[:block.index("def run(self")]
    assert "_dispatch_rate(" in block


def test_the_progress_display_still_uses_the_measured_rate(plugin_module):
    """The displayed estimate is where accuracy matters and where being wrong
    costs nothing, so it is NOT clamped."""
    import pathlib
    src = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    block = src[src.index("if stream_count:"):]
    block = block[:block.index("initial_eta_str")]
    assert "_observed_rate" in block
    assert "max(" not in block
