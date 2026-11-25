"""Stream-Mapparr's storage: where its private state lives and what moves over from /data.

The logic (resolving the data directory, carrying the old /data state over, the tally tail and
binding the state constants) is in plugin_storage.py, a vendored copy of _shared/plugin_storage.py.
This module holds only what belongs to this plugin: the Spec, the table of state paths, the names
kept beside the data directories and the rollout switch. Each public function passes SPEC to the
vendored module, so callers see the same names they always did.
"""
from __future__ import annotations

try:
    from . import plugin_storage as _ps
except ImportError:  # loaded as a bare module, as plugin.py allows
    import plugin_storage as _ps

# Rollout switch for this release only: remove in the release after next, with the dual-path
# branches it guards and tests/test_storage_rollout.py. While it is on, a worker on this code
# also honours the old /data state files that workers on the previous release still write
# (the slot claim, the M3U lock and pending flag, the operation lock, the tally tail).
ROLLOUT_DUAL_PATHS = True

# (constant name, kind, rel under the data dir). rel is None for the rows that live
# beside the data dirs or in the shared reports dir; those are named in _BESIDE.
_BIND_TABLE = (
    ("SETTINGS_FILE", "file", "settings.json"),
    ("PROCESSED_DATA_FILE", "file", "processed.json"),
    ("PROGRESS_STATE_FILE", "file", "progress.json"),
    ("LAST_RESULTS_FILE", "file", "last_results.json"),
    ("PICK_LIST_NAMES_FILE", "file", "pick_list_names.json"),
    ("RUN_TIMING_FILE", "file", "run_timing.json"),
    ("THROUGHPUT_CACHE_FILE", "file", "throughput_cache.json"),
    ("SCHEDULED_RUN_FILE", "file", "scheduled_run.json"),
    ("MATCH_TALLY_FILE", "tally", "match_counts.jsonl"),
    ("OPERATION_LOCK_FILE", "nocopy", None),
    ("M3U_REFRESH_LOCK_FILE", "nocopy", None),
    ("M3U_REFRESH_PENDING_FILE", "nocopy", None),
    ("READOUT_FALLBACK_DIR", "dir", "readouts"),
    ("CHANNEL_SNAPSHOT_DIR", "dir", "snapshots"),
    ("USAGE_DATA_DIR", "usage_dir", "usage"),
    ("SCHEDULER_LAST_RUN_FILE", "beside", None),
    ("SCHEDULER_LOCK_FILE", "nocopy", None),
    ("REPORT_DIR", "dir", None),
)

_BESIDE = {
    "SCHEDULER_LAST_RUN_FILE": ".scheduler_last_run.json",
    "SCHEDULER_LOCK_FILE": ".scheduler.lock",
    "OPERATION_LOCK_FILE": ".operation.lock",
    "M3U_REFRESH_LOCK_FILE": ".m3u_refresh.lock",
    "M3U_REFRESH_PENDING_FILE": ".m3u_refresh_pending",
}

# The tally file names are the ones already on disk for this plugin; do not change them.
SPEC = _ps.Spec(
    label="Stream-Mapparr",
    state_name="_stream_mapparr_storage_state",
    table=_BIND_TABLE,
    beside=_BESIDE,
    reports_subdir="stream-mapparr",
    usage_subdir="stream-mapparr",
    tally_offsets="match_counts.offsets.json",
    tally_lock="match_counts.lock",
    reports_constant="REPORT_DIR",
)

Layout = _ps.Layout
Item = _ps.Item
MigrateResult = _ps.MigrateResult
atomic_write_bytes = _ps.atomic_write_bytes
atomic_write_json = _ps.atomic_write_json


def resolve(plugin_dir, key, logger):
    """Resolve the data directory for the plugin folder plugin_dir. See plugin_storage.resolve."""
    return _ps.resolve(SPEC, plugin_dir, key, logger)


def migrate(layout, items, logger, version=None):
    """Copy the old state into the data dir once. See plugin_storage.migrate."""
    return _ps.migrate(SPEC, layout, items, logger, version=version)


def sync_tally_tail(layout, tally_path, logger):
    """Append the tally's new old-source bytes to it. See plugin_storage.sync_tally_tail."""
    return _ps.sync_tally_tail(SPEC, layout, tally_path, logger)


def items_for(cfg, layout):
    """The moves bind hands to migrate. See plugin_storage.items_for."""
    return _ps.items_for(SPEC, cfg, layout)


def bind(cfg, plugin_file, logger):
    """Resolve, rebind the state constants and carry the old state over. See plugin_storage.bind."""
    return _ps.bind(SPEC, cfg, plugin_file, logger)


def check_context(layout, context, logger):
    """Warn once when Dispatcharr's data_dir differs from the resolved one. See plugin_storage."""
    return _ps.check_context(SPEC, layout, context, logger)
