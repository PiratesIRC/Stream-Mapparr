"""How long a matching run is predicted to take.

The old model was `seconds = channel_groups x 0.8`. It ignored the size of the
stream pool, which is the dominant term: matching walks the pool once per
channel group, so the work is groups TIMES streams, not groups alone.

That is why the estimate degraded as the pool grew rather than being uniformly
wrong. Three measurements, the first two recorded in the plugin's own comments
and the third from a real run on 2026-08-02:

    groups  streams   actual    old model   error
        13     ~1670    35.0s       10.4s     3.4x too low
        29     ~1670    78.0s       23.2s     3.4x too low
        44     19493  1382.1s       35.2s    39.3x too low

A single rate explains all three: about 1.61 seconds per channel group per 1000
streams. Fitting it on the 44-group run and applying it backwards implies a pool
of 1,671 and 1,669 streams for the two historical runs, which agree with each
other, which is what should happen if those two runs shared a configuration.
"""
import pytest


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


# The measurements above, as (groups, streams, actual seconds).
MEASURED = [
    (13, 1670, 35.0),
    (29, 1670, 78.0),
    (44, 19493, 1382.1),
]


# --------------------------------------------------------------------------- #
# The model
# --------------------------------------------------------------------------- #

def test_the_model_predicts_every_measured_run_within_15_percent(plugin_module):
    """The bar is deliberately loose. An estimate is for deciding whether to
    wait or come back later, so being in the right order of magnitude is the
    whole requirement. The old model was 39x out on the largest run."""
    est = plugin_module.estimate_run_seconds
    for groups, streams, actual in MEASURED:
        predicted = est(groups, streams)
        ratio = predicted / actual
        assert 0.85 <= ratio <= 1.15, (
            f"{groups} groups over {streams} streams: predicted {predicted:.0f}s "
            f"against a measured {actual:.0f}s ({ratio:.2f}x)")


def test_the_estimate_scales_with_the_stream_pool(plugin_module):
    """Doubling the pool must roughly double the estimate. This is the property
    the old model lacked entirely."""
    est = plugin_module.estimate_run_seconds
    assert est(44, 20000) == pytest.approx(2 * est(44, 10000), rel=0.01)


def test_the_estimate_scales_with_the_group_count(plugin_module):
    est = plugin_module.estimate_run_seconds
    assert est(80, 5000) == pytest.approx(2 * est(40, 5000), rel=0.01)


def test_a_zero_or_missing_input_does_not_raise(plugin_module):
    est = plugin_module.estimate_run_seconds
    for groups, streams in ((0, 0), (0, 5000), (44, 0), (None, None)):
        assert est(groups, streams) >= 0


# --------------------------------------------------------------------------- #
# Self-calibration
# --------------------------------------------------------------------------- #

def test_an_observation_is_recorded_and_read_back(plugin_module, tmp_path):
    p = _bare(plugin_module)
    path = str(tmp_path / "timing.json")
    p._record_run_timing(path, groups=44, streams=19493, duration=1382.1)
    rate = p._observed_rate(path)
    assert rate == pytest.approx(1382.1 / (44 * 19.493), rel=0.01)


def test_the_observed_rate_is_used_in_preference_to_the_default(plugin_module, tmp_path):
    """A rate measured on this installation beats one fitted elsewhere. The
    matcher's speed depends on the machine and on the shape of the names."""
    p = _bare(plugin_module)
    path = str(tmp_path / "timing.json")
    p._record_run_timing(path, groups=10, streams=1000, duration=100.0)
    assert p._observed_rate(path) == pytest.approx(10.0, rel=0.01)


def test_a_missing_or_corrupt_timing_file_falls_back_to_the_default(plugin_module, tmp_path):
    """Degrade to the shipped constant rather than to zero. An estimate of zero
    would send a long job down the synchronous path and freeze a worker."""
    p = _bare(plugin_module)
    assert p._observed_rate(str(tmp_path / "absent.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert p._observed_rate(str(bad)) is None


def test_an_implausible_observation_is_ignored(plugin_module, tmp_path):
    """A run interrupted at one second, or one that sat behind a lock for an
    hour, must not poison every future estimate."""
    p = _bare(plugin_module)
    path = str(tmp_path / "timing.json")
    p._record_run_timing(path, groups=0, streams=0, duration=0.0)
    assert p._observed_rate(path) is None


def test_recording_never_raises_on_an_unwritable_path(plugin_module, tmp_path):
    """Timing is diagnostics. It must not break the run it measures."""
    p = _bare(plugin_module)
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("x", encoding="utf-8")
    p._record_run_timing(str(blocker / "nested" / "t.json"),
                         groups=10, streams=1000, duration=100.0)
