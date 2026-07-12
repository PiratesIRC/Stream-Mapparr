"""Tests for the sync-vs-background dispatch decision (bug-117).

Dispatcharr's uWSGI runs gevent with `gevent-early-monkey-patch = true`, so the
matcher's CPU-bound loop is a greenlet that never yields: running it inside a
request freezes the ENTIRE worker (all 400 async cores), not just that request.
Being wrong here costs a hung web UI and an nginx 504 (proxy_read_timeout=300s),
so the gate must be strongly biased toward the background path.

Reporter's numbers (Dispatcharr 0.27.2, issue #37 thread): 13 groups took 35s and
29 groups took 1m18s => ~2.7s/group, i.e. ~3.4x ESTIMATED_SECONDS_PER_ITEM (0.8).
The 29-group run therefore estimated 23.2s, slipped under the old 25s threshold,
ran synchronously, and wedged a uWSGI worker for 78s.
"""
import pytest


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


LOCKABLE = {
    "preview_changes",
    "add_streams_to_channels",
    "sort_streams",
    "match_us_ota_only",
    "manage_channel_visibility",
    "probe_throughput",
}

# Measured on the reporter's box; the estimate constant is tuned ~3.4x optimistic.
OBSERVED_SECONDS_PER_GROUP = 2.7


def _eta_for(plugin_module, groups):
    """The ETA the plugin would compute for `groups` channel groups."""
    return groups * plugin_module.PluginConfig.ESTIMATED_SECONDS_PER_ITEM


# --------------------------------------------------------------------------- #
# Regression: the exact run that wedged the reporter's worker
# --------------------------------------------------------------------------- #
def test_29_group_run_does_not_go_sync(plugin_module):
    """bug-117: 29 groups estimated 23.2s (< old 25s threshold) but really took 78s."""
    plugin = _bare(plugin_module)
    eta = _eta_for(plugin_module, 29)

    assert eta < 25, "precondition: this is what slipped under the old threshold"
    assert plugin._should_run_sync("add_streams_to_channels", eta, LOCKABLE) is False


@pytest.mark.parametrize("groups", [13, 29, 50, 120])
def test_real_world_group_counts_all_background(plugin_module, groups):
    """Anything big enough to be felt by a user must not block a uWSGI worker."""
    plugin = _bare(plugin_module)
    eta = _eta_for(plugin_module, groups)
    assert plugin._should_run_sync("add_streams_to_channels", eta, LOCKABLE) is False


def test_sync_gate_covers_worst_case_actual_runtime(plugin_module):
    """The safety factor must bracket the observed 3.4x underestimate.

    Whatever we still allow to run sync must, at the OBSERVED per-group cost,
    stay far below nginx's 300s proxy_read_timeout.
    """
    plugin = _bare(plugin_module)
    cfg = plugin_module.PluginConfig
    worst_sync_groups = max(
        (g for g in range(1, 2000)
         if plugin._should_run_sync("add_streams_to_channels", _eta_for(plugin_module, g), LOCKABLE)),
        default=0,
    )
    worst_actual = worst_sync_groups * OBSERVED_SECONDS_PER_GROUP
    assert worst_actual < 30, (
        f"sync path admits {worst_sync_groups} groups = ~{worst_actual:.0f}s of real work "
        f"inside a gevent worker (threshold={cfg.SYNC_THRESHOLD_SECONDS}s, "
        f"factor={cfg.ETA_SAFETY_FACTOR})"
    )


# --------------------------------------------------------------------------- #
# The gate's other arms
# --------------------------------------------------------------------------- #
def test_unknown_eta_goes_background(plugin_module):
    """No cached processed_data (first run) => we cannot size the job => background."""
    plugin = _bare(plugin_module)
    assert plugin._should_run_sync("add_streams_to_channels", None, LOCKABLE) is False


def test_non_lockable_action_never_sync(plugin_module):
    plugin = _bare(plugin_module)
    assert plugin._should_run_sync("view_check_progress", 1.0, LOCKABLE) is False


def test_trivial_job_may_still_run_sync(plugin_module):
    """A one-group job is cheap enough to answer inline with a real toast."""
    plugin = _bare(plugin_module)
    assert plugin._should_run_sync("add_streams_to_channels", _eta_for(plugin_module, 1), LOCKABLE) is True
