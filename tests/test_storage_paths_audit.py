"""Every /data path literal in the shipped modules is one the storage design accounts for.

storage.bind rebinds the PluginConfig state constants to the plugin data directory at import.
The literals that stay are the legacy paths storage.bind records in LEGACY_PATHS (the carry-over
source and the fallback when the data directory cannot be trusted), plus the shared exports
folder and the files that belong to other projects or to vendored clients. A new /data literal
anywhere else is a path the data-dir move does not cover, so it fails here.

storage.py itself names the legacy paths in its docstrings and the tests tree is excluded.
A literal counts only when it starts with /data and contains no whitespace, so help text that
mentions a path is not a path literal.
"""
import ast
import pathlib

PLUGIN_DIR = pathlib.Path(__file__).resolve().parent.parent / "Stream-Mapparr"

ALLOWED = {
    # PluginConfig state constants: the legacy paths storage.bind moves (LEGACY_PATHS).
    "/data/stream_mapparr_readouts",
    "/data/stream_mapparr_snapshots",
    "/data/plugin_stats",
    "/data/stream_mapparr_reports",
    "/data/stream_mapparr_processed.json",
    "/data/stream_mapparr_settings.json",
    "/data/stream_mapparr_operation.lock",
    "/data/stream_mapparr_scheduler_last_run.json",
    "/data/stream_mapparr_match_counts.jsonl",
    "/data/stream_mapparr_scheduler.lock",
    "/data/stream_mapparr_m3u_refresh.lock",
    "/data/stream_mapparr_m3u_refresh_pending",
    "/data/stream_mapparr_progress.json",
    "/data/stream_mapparr_last_results.json",
    "/data/stream_mapparr_pick_list_names.json",
    "/data/stream_mapparr_run_timing.json",
    "/data/stream_mapparr_scheduled_run.json",
    "/data/stream_mapparr_throughput_cache.json",
    # Kept as they are: the shared exports folder, IPTV Checker's file, and the copies the
    # vendored notify and usage clients and notify_bridge name for their own fallbacks.
    "/data/exports",
    "/data/iptv_checker_progress.json",
    "/data/newsflasharr",
}


def _literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if value.startswith("/data") and not any(ch.isspace() for ch in value):
                found.add(value)
    return found


def test_every_data_literal_is_accounted_for():
    found = set()
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name == "storage.py":
            continue
        found |= _literals(path)
    strays = found - ALLOWED
    unused = ALLOWED - found
    assert not strays, "unaccounted /data literals: %s" % sorted(strays)
    assert not unused, "allowed /data literals no longer in the code: %s" % sorted(unused)
