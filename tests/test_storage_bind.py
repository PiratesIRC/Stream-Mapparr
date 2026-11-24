"""storage.bind moves the state paths into the data dir at import and carries the old state over.

Every path is a temp directory standing in for /data and <plugins>/_data. The session plugin
module stays in legacy mode. A test that constructs Plugin() after a non-legacy bind loads a
fresh copy of plugin.py under its own package name, with its /data literals pointed into
tmp_path, so no rebound path leaks into other tests.
"""
import importlib
import itertools
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import PLUGIN_DIR, _PKG

STATE = "_stream_mapparr_storage_state"
SCHED_STATE = "_stream_mapparr_scheduler_state"

# constant name -> legacy leaf name under the old /data root
OLD_LEAVES = {
    "SETTINGS_FILE": "stream_mapparr_settings.json",
    "PROCESSED_DATA_FILE": "stream_mapparr_processed.json",
    "PROGRESS_STATE_FILE": "stream_mapparr_progress.json",
    "LAST_RESULTS_FILE": "stream_mapparr_last_results.json",
    "PICK_LIST_NAMES_FILE": "stream_mapparr_pick_list_names.json",
    "RUN_TIMING_FILE": "stream_mapparr_run_timing.json",
    "THROUGHPUT_CACHE_FILE": "stream_mapparr_throughput_cache.json",
    "SCHEDULED_RUN_FILE": "stream_mapparr_scheduled_run.json",
    "MATCH_TALLY_FILE": "stream_mapparr_match_counts.jsonl",
    "OPERATION_LOCK_FILE": "stream_mapparr_operation.lock",
    "M3U_REFRESH_LOCK_FILE": "stream_mapparr_m3u_refresh.lock",
    "M3U_REFRESH_PENDING_FILE": "stream_mapparr_m3u_refresh_pending",
    "READOUT_FALLBACK_DIR": "stream_mapparr_readouts",
    "CHANNEL_SNAPSHOT_DIR": "stream_mapparr_snapshots",
    "USAGE_DATA_DIR": "plugin_stats",
    "SCHEDULER_LAST_RUN_FILE": "stream_mapparr_scheduler_last_run.json",
    "SCHEDULER_LOCK_FILE": "stream_mapparr_scheduler.lock",
    "REPORT_DIR": "stream_mapparr_reports",
}

_seq = itertools.count()


class Log:
    def __init__(self):
        self.errors = []
        self.infos = []

    def info(self, *args):
        self.infos.append(args)

    def error(self, *args):
        self.errors.append(args)

    def warning(self, *args):
        pass

    def debug(self, *args):
        pass


@pytest.fixture
def storage():
    return importlib.import_module(f"{_PKG}.storage")


@pytest.fixture(autouse=True)
def _clear_process_state():
    sys.modules.pop(STATE, None)
    sys.modules.pop(SCHED_STATE, None)
    yield
    sys.modules.pop(STATE, None)
    sys.modules.pop(SCHED_STATE, None)


@pytest.fixture
def roots(tmp_path):
    root = SimpleNamespace(
        old=str(tmp_path / "data"),
        plugins=str(tmp_path / "plugins"),
        data=str(tmp_path / "plugins" / "_data" / "stream-mapparr"),
    )
    os.makedirs(root.old, exist_ok=True)
    os.makedirs(os.path.join(root.plugins, "_data"))
    return root


def make_cfg(old_root, key="stream-mapparr"):
    """A fake PluginConfig whose state literals stand in for /data under old_root."""
    attrs = {name: os.path.join(old_root, leaf) for name, leaf in OLD_LEAVES.items()}
    attrs.update(PLUGIN_DB_KEY=key, PLUGIN_VERSION="1.2.3")
    return type("FakeConfig", (), attrs)


def layout_of(storage, roots, data=None, key="stream-mapparr"):
    return storage.Layout(False, data or roots.data, roots.plugins, key, "test")


def use_layout(monkeypatch, storage, layout):
    monkeypatch.setattr(storage, "resolve", lambda *a, **k: layout)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def plugin_file():
    return str(PLUGIN_DIR / "plugin.py")


def load_bound_plugin(monkeypatch, storage, roots, layout, before_exec=None):
    """A fresh copy of plugin.py bound to layout, its /data literals moved into roots.old.

    The package entry's __path__ is PLUGIN_DIR, so the copy's relative imports resolve to the
    real siblings. storage.resolve is patched before the module body runs.
    """
    pkg = f"{_PKG}_bind{next(_seq)}"
    pkg_mod = types.ModuleType(pkg)
    pkg_mod.__path__ = [str(PLUGIN_DIR)]
    sys.modules[pkg] = pkg_mod
    fresh_storage = importlib.import_module(f"{pkg}.storage")
    assert fresh_storage is not storage
    monkeypatch.setattr(fresh_storage, "resolve", lambda *a, **k: layout)
    monkeypatch.setattr(fresh_storage, "REFUSE_REAL_DATA", True)
    if before_exec is not None:
        before_exec(fresh_storage)
    source = Path(plugin_file()).read_text(encoding="utf-8")
    source = source.replace('"/data/', '"' + roots.old.replace("\\", "/") + "/")
    name = f"{pkg}.plugin"
    mod = types.ModuleType(name)
    mod.__file__ = plugin_file()
    mod.__package__ = pkg
    sys.modules[name] = mod
    exec(compile(source, plugin_file(), "exec"), mod.__dict__)
    return mod


def patch_plugin(monkeypatch, mod, db_settings=None, started=None):
    """No DB read beyond db_settings, and the scheduler start is recorded, not run."""
    cls = mod.Plugin
    monkeypatch.setattr(cls, "_settings_from_db", lambda self: db_settings, raising=False)
    calls = started if started is not None else []
    monkeypatch.setattr(cls, "_start_background_scheduler", lambda self, s: calls.append(dict(s)))
    return calls


# ---------------------------------------------------------------------------
# bind on the fake config
# ---------------------------------------------------------------------------

def test_legacy_mode_rebinds_nothing(storage, roots, monkeypatch):
    cfg = make_cfg(roots.old)
    before = {n: getattr(cfg, n) for n in OLD_LEAVES}
    use_layout(monkeypatch, storage, storage.Layout(True, None, None, "stream-mapparr", "test"))
    storage.bind(cfg, plugin_file(), Log())
    assert {n: getattr(cfg, n) for n in OLD_LEAVES} == before
    assert cfg.STORAGE_MIGRATION is None
    assert cfg.STORAGE_LAYOUT.legacy is True


def test_bind_rebinds_every_table_row(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    use_layout(monkeypatch, storage, layout)
    cfg = make_cfg(roots.old)
    storage.bind(cfg, plugin_file(), Log())
    d = layout.data_dir
    assert cfg.STORAGE_LAYOUT is layout
    assert cfg.STORAGE_MIGRATION is not None
    assert cfg.SETTINGS_FILE == os.path.join(d, "settings.json")
    assert cfg.PROCESSED_DATA_FILE == os.path.join(d, "processed.json")
    assert cfg.PROGRESS_STATE_FILE == os.path.join(d, "progress.json")
    assert cfg.LAST_RESULTS_FILE == os.path.join(d, "last_results.json")
    assert cfg.PICK_LIST_NAMES_FILE == os.path.join(d, "pick_list_names.json")
    assert cfg.RUN_TIMING_FILE == os.path.join(d, "run_timing.json")
    assert cfg.THROUGHPUT_CACHE_FILE == os.path.join(d, "throughput_cache.json")
    assert cfg.SCHEDULED_RUN_FILE == os.path.join(d, "scheduled_run.json")
    assert cfg.MATCH_TALLY_FILE == os.path.join(d, "match_counts.jsonl")
    assert cfg.OPERATION_LOCK_FILE == os.path.join(d, "operation.lock")
    assert cfg.M3U_REFRESH_LOCK_FILE == os.path.join(d, "m3u_refresh.lock")
    assert cfg.M3U_REFRESH_PENDING_FILE == os.path.join(d, "m3u_refresh_pending")
    assert cfg.READOUT_FALLBACK_DIR == os.path.join(d, "readouts")
    assert cfg.CHANNEL_SNAPSHOT_DIR == os.path.join(d, "snapshots")
    assert cfg.USAGE_DATA_DIR == os.path.join(d, "usage")
    assert cfg.SCHEDULER_LAST_RUN_FILE == layout.beside(".scheduler_last_run.json")
    assert cfg.SCHEDULER_LOCK_FILE == layout.beside(".scheduler.lock")
    assert cfg.REPORT_DIR == layout.shared_reports_dir()


def test_legacy_paths_recorded(storage, roots, monkeypatch):
    use_layout(monkeypatch, storage, layout_of(storage, roots))
    cfg = make_cfg(roots.old)
    storage.bind(cfg, plugin_file(), Log())
    assert set(cfg.LEGACY_PATHS) == set(OLD_LEAVES)
    for name, leaf in OLD_LEAVES.items():
        assert cfg.LEGACY_PATHS[name] == os.path.join(roots.old, leaf)


def test_migrate_failure_restores_legacy_paths(storage, roots, monkeypatch):
    use_layout(monkeypatch, storage, layout_of(storage, roots))

    def boom(*args, **kwargs):
        raise OSError("lock failed")

    monkeypatch.setattr(storage, "migrate", boom)
    cfg = make_cfg(roots.old)
    before = {n: getattr(cfg, n) for n in OLD_LEAVES}
    log = Log()
    storage.bind(cfg, plugin_file(), log)
    assert {n: getattr(cfg, n) for n in OLD_LEAVES} == before
    assert cfg.STORAGE_MIGRATION is None
    assert cfg.STORAGE_LAYOUT.legacy is True
    assert cfg.STORAGE_LAYOUT.reason == "migrate failed"
    assert len(log.errors) == 1


def test_reimport_does_not_recopy(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    use_layout(monkeypatch, storage, layout)
    write(os.path.join(roots.old, "stream_mapparr_settings.json"), '{"a": 1}')
    first = make_cfg(roots.old)
    storage.bind(first, plugin_file(), Log())
    assert first.STORAGE_MIGRATION.skipped_pass is False
    second = make_cfg(roots.old)
    storage.bind(second, plugin_file(), Log())
    assert second.STORAGE_MIGRATION.skipped_pass is True


def test_install_id_blocked_flag_set_on_failed_copy(storage, roots, monkeypatch):
    use_layout(monkeypatch, storage, layout_of(storage, roots))
    write(os.path.join(roots.old, "plugin_stats", "stream-mapparr", "install_id"), "abc")
    real_copy = storage._copy_one

    def fail_install_id(dst, sources, copied):
        if os.path.basename(dst) == "install_id":
            raise OSError("denied")
        return real_copy(dst, sources, copied)

    monkeypatch.setattr(storage, "_copy_one", fail_install_id)
    cfg = make_cfg(roots.old)
    storage.bind(cfg, plugin_file(), Log())
    assert cfg.STORAGE_MIGRATION.install_id_blocked is True
    assert not os.path.exists(os.path.join(cfg.USAGE_DATA_DIR, "stream-mapparr", "install_id"))


# ---------------------------------------------------------------------------
# Plugin() after a non-legacy bind (fresh plugin.py copy)
# ---------------------------------------------------------------------------

def test_upgrade_settings_loaded_from_data_dir(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    write(os.path.join(roots.old, "stream_mapparr_settings.json"),
          json.dumps({"scheduled_times": "0505"}))
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    calls = patch_plugin(monkeypatch, mod, db_settings=None)  # cannot ask the DB
    plugin = mod.Plugin()
    assert plugin.settings_file == os.path.join(layout.data_dir, "settings.json")
    assert plugin.saved_settings == {"scheduled_times": "0505"}
    assert calls and calls[-1] == {"scheduled_times": "0505"}
    assert read(os.path.join(layout.data_dir, "settings.json")) == read(
        os.path.join(roots.old, "stream_mapparr_settings.json"))


def test_upgrade_reads_034_form_settings(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    old = {"scheduled_time_list": ["0505", "1200"]}
    write(os.path.join(roots.old, "stream_mapparr_settings.json"), json.dumps(old))
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    monkeypatch.setattr(mod.Plugin, "_host_draws_034_form", staticmethod(lambda: True))
    patch_plugin(monkeypatch, mod, db_settings=None)
    plugin = mod.Plugin()
    assert plugin.saved_settings == plugin._legacy_settings(old)
    assert plugin.saved_settings["scheduled_times"] == "0505,1200"


def test_both_present_new_wins_old_untouched(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    old_text = json.dumps({"scheduled_times": "0505"})
    new_text = json.dumps({"scheduled_times": "0700"})
    write(os.path.join(roots.old, "stream_mapparr_settings.json"), old_text)
    write(os.path.join(layout.data_dir, "settings.json"), new_text)
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    patch_plugin(monkeypatch, mod, db_settings=None)
    mod.Plugin()
    assert read(os.path.join(layout.data_dir, "settings.json")) == new_text
    assert read(os.path.join(roots.old, "stream_mapparr_settings.json")) == old_text


def test_db_schedule_wins_and_new_file_rewritten(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    old_text = json.dumps({"scheduled_times": "0505"})
    write(os.path.join(roots.old, "stream_mapparr_settings.json"), old_text)
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    calls = patch_plugin(monkeypatch, mod, db_settings={"scheduled_times": "0600"})
    plugin = mod.Plugin()
    assert plugin.saved_settings["scheduled_times"] == "0600"
    assert calls[-1]["scheduled_times"] == "0600"
    assert json.loads(read(os.path.join(layout.data_dir, "settings.json")))["scheduled_times"] == "0600"
    assert read(os.path.join(roots.old, "stream_mapparr_settings.json")) == old_text


def test_save_settings_writes_only_new_path_and_is_atomic(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    old_text = json.dumps({"scheduled_times": "0505"})
    write(os.path.join(roots.old, "stream_mapparr_settings.json"), old_text)
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    patch_plugin(monkeypatch, mod, db_settings=None)
    plugin = mod.Plugin()
    replaced = []
    real_replace = os.replace

    def record(src, dst):
        replaced.append((src, dst))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", record)
    plugin._save_settings({"scheduled_times": "0900"})
    assert replaced and all(dst == os.path.join(layout.data_dir, "settings.json") for _src, dst in replaced)
    assert json.loads(read(os.path.join(layout.data_dir, "settings.json")))["scheduled_times"] == "0900"
    assert read(os.path.join(roots.old, "stream_mapparr_settings.json")) == old_text


def test_folder_key_dir_beats_old_data_file(storage, roots, monkeypatch):
    hub = os.path.join(roots.plugins, "_data", "hubslug")
    layout = layout_of(storage, roots, data=hub)
    write(os.path.join(roots.plugins, "_data", "stream-mapparr", "settings.json"),
          json.dumps({"scheduled_times": "0101"}))
    write(os.path.join(roots.old, "stream_mapparr_settings.json"),
          json.dumps({"scheduled_times": "0202"}))
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    patch_plugin(monkeypatch, mod, db_settings=None)
    plugin = mod.Plugin()
    assert plugin.saved_settings["scheduled_times"] == "0101"
    assert json.loads(read(os.path.join(hub, "settings.json")))["scheduled_times"] == "0101"


def test_fresh_install_no_files(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    calls = patch_plugin(monkeypatch, mod, db_settings={"scheduled_times": "0700"})
    plugin = mod.Plugin()
    assert plugin.saved_settings == {"scheduled_times": "0700"}
    assert calls[-1]["scheduled_times"] == "0700"


def test_install_id_blocked_never_mints(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    usage_old = os.path.join(roots.old, "plugin_stats", "stream-mapparr")
    write(os.path.join(usage_old, "install_id"), "abc")

    def fail_install_id_copy(fresh_storage):
        real_copy = fresh_storage._copy_one

        def fail_install_id(dst, sources, copied):
            if os.path.basename(dst) == "install_id":
                raise OSError("denied")
            return real_copy(dst, sources, copied)

        monkeypatch.setattr(fresh_storage, "_copy_one", fail_install_id)

    mod = load_bound_plugin(monkeypatch, storage, roots, layout, before_exec=fail_install_id_copy)
    usage_client = importlib.import_module(mod.__package__ + ".usage_client")
    real_load = usage_client.load_install_id
    minted = []
    monkeypatch.setattr(usage_client, "load_install_id",
                        lambda directory: minted.append(directory) or real_load(directory))
    # Opted in (settings {}), no real lock needed on Windows, the background start runs inline,
    # and the network call is recorded instead of sent.
    usage = mod.USAGE
    monkeypatch.setattr(usage, "settings_fn", lambda: {})
    monkeypatch.setattr(usage, "_lock", lambda fd: True)
    started = []
    monkeypatch.setattr(usage, "_start", lambda fn: started.append(fn))
    sent = []
    monkeypatch.setattr(usage, "_http", lambda method, url, body: sent.append(url) or 202)
    patch_plugin(monkeypatch, mod, db_settings=None)
    assert mod.PluginConfig.STORAGE_MIGRATION.install_id_blocked is True
    usage.report({}, Log(), force=True)
    for fn in started:
        fn()
    mod.Plugin()
    install = os.path.join(layout.data_dir, "usage", "stream-mapparr", "install_id")
    assert not os.path.exists(install)
    assert minted == []
    assert sent == []


def test_refuse_real_blocks_plugin_writes(storage, monkeypatch):
    monkeypatch.setattr(storage, "REFUSE_REAL_DATA", True)
    with pytest.raises(RuntimeError):
        storage.atomic_write_json("/data/x.json", {})
    with pytest.raises(RuntimeError):
        storage.atomic_write_bytes("/data/x.json", b"{}")
    with pytest.raises(RuntimeError):
        storage._append_bytes("/data/match_counts.jsonl", b"x\n")


@pytest.mark.parametrize("breaks", ["items_for", "resolve"])
def test_any_bind_exception_degrades_to_legacy(storage, roots, monkeypatch, breaks):
    def boom(*args, **kwargs):
        raise ValueError("broken")

    use_layout(monkeypatch, storage, layout_of(storage, roots))  # non-legacy, so items_for runs
    monkeypatch.setattr(storage, breaks, boom)
    cfg = make_cfg(roots.old)
    before = {n: getattr(cfg, n) for n in OLD_LEAVES}
    log = Log()
    layout = storage.bind(cfg, plugin_file(), log)
    assert layout.legacy is True
    assert cfg.STORAGE_LAYOUT.legacy is True
    assert cfg.STORAGE_MIGRATION is None
    assert {n: getattr(cfg, n) for n in OLD_LEAVES} == before
    assert len(log.errors) == 1


class _NotifyClient:
    @staticmethod
    def notify(source, title, **kw):
        return True


def test_reports_copied_and_new_dir_used(storage, roots, monkeypatch):
    layout = layout_of(storage, roots)
    report_old = os.path.join(roots.old, "stream_mapparr_reports")
    names = [f"stream_mapparr_report_20260101_0000{i:02d}.html" for i in range(12)]
    for i, name in enumerate(names):
        path = os.path.join(report_old, name)
        write(path, f"<p>{i}</p>")
        os.utime(path, (1_000_000 + i, 1_000_000 + i))
    old_files = sorted(os.listdir(report_old))
    mod = load_bound_plugin(monkeypatch, storage, roots, layout)
    shared = layout.shared_reports_dir()
    assert mod.PluginConfig.REPORT_DIR == shared
    assert read(os.path.join(shared, names[0])) == "<p>0</p>"
    if os.name == "posix":
        assert (os.stat(shared).st_mode & 0o777) == 0o755
    # Age the copies too, so a prune in the shared dir really deletes something.
    for name in os.listdir(shared):
        os.utime(os.path.join(shared, name), (1_000_000, 1_000_000))

    inst = mod.Plugin.__new__(mod.Plugin)
    reports_mod = inst._reports()
    seen = []
    real_write = reports_mod.write_report

    def recording_write(model, report_dir, now):
        seen.append(report_dir)
        return real_write(model, report_dir, now)

    monkeypatch.setattr(reports_mod, "write_report", recording_write)
    monkeypatch.setattr(inst, "_notify_client", lambda: _NotifyClient)
    inst._build_and_emit_reports(
        {"notify_enabled": True, "notify_report_on": "every_run"},
        types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None,
                              warning=lambda *a, **k: None, error=lambda *a, **k: None),
        [{"channel_name": "Sky News", "stream_names": ["SKY NEWS HD"]}],
        ["provider.tv"],
        is_scheduled=False)
    assert seen == [mod.PluginConfig.REPORT_DIR]
    # The prune ran on the shared dir (8 html kept of 13) ...
    assert len([n for n in os.listdir(shared) if n.endswith(".html")]) <= 9
    # ... and the old /data reports were not touched.
    assert sorted(os.listdir(report_old)) == old_files
    for i, name in enumerate(names):
        assert read(os.path.join(report_old, name)) == f"<p>{i}</p>"


# ---------------------------------------------------------------------------
# Legacy mode through Plugin()
# ---------------------------------------------------------------------------

def test_legacy_mode_reads_and_writes_only_old_path(plugin_module, tmp_path, monkeypatch):
    cfg = plugin_module.PluginConfig
    assert cfg.STORAGE_LAYOUT.legacy is True
    write(cfg.SETTINGS_FILE, json.dumps({"scheduled_times": "0505"}))
    monkeypatch.setattr(plugin_module.Plugin, "_settings_from_db", lambda self: None)
    calls = []
    monkeypatch.setattr(plugin_module.Plugin, "_start_background_scheduler",
                        lambda self, s: calls.append(dict(s)))
    plugin = plugin_module.Plugin()
    assert plugin.settings_file == cfg.SETTINGS_FILE
    assert plugin.saved_settings["scheduled_times"] == "0505"
    plugin._save_settings({"scheduled_times": "0800"})
    assert json.loads(read(cfg.SETTINGS_FILE))["scheduled_times"] == "0800"
    assert not os.path.exists(os.path.join(str(tmp_path), "plugins"))
