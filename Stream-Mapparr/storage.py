"""Resolve the plugin's private data directory under Dispatcharr's plugin data root.

Dispatcharr 0.34 gives each plugin a directory at <plugins>/_data/<storage key>. This
module finds that directory and checks it. It stays stdlib-only and imports nothing from
plugin.py, and every Dispatcharr import happens inside a function so the module loads
(and its tests run) without Dispatcharr present.

When the directory cannot be trusted, resolve returns a legacy Layout: the caller keeps
using the old /data paths for that process.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import time
import types
from dataclasses import dataclass, field

try:
    import fcntl
except ImportError:  # Windows: no flock, so the copy runs unlocked
    fcntl = None

REFUSE_REAL_DATA = False


@dataclass(frozen=True)
class Layout:
    """Where this process keeps its private state.

    A legacy Layout has data_dir and plugins_dir set to None, and methods that need
    plugins_dir cannot be called on it.
    """

    legacy: bool
    data_dir: str | None
    plugins_dir: str | None
    key: str
    reason: str

    def beside(self, suffix: str) -> str:
        """Path of a file kept beside the data directories, in <plugins>/_data/."""
        return os.path.join(self.plugins_dir, "_data", self.key + suffix)

    def shared_reports_dir(self) -> str:
        """Directory Newsflasharr reads reports from, shared across plugin slugs."""
        return os.path.join(self.plugins_dir, "_shared", "newsflasharr", "stream-mapparr")


def resolve(plugin_dir: str, key: str, logger) -> Layout:
    """Resolve the data directory for the plugin folder plugin_dir.

    plugin_dir is the folder holding this module. key is the plugin's registry key
    (PluginConfig.PLUGIN_DB_KEY). Resolution runs on every call and is not cached.
    """
    plugins_dir = os.path.dirname(os.path.abspath(plugin_dir))
    failures = []
    data_dir, reason = _from_module_context(plugin_dir, failures)
    if data_dir is None:
        data_dir, reason = _from_registry(key, failures)
    if data_dir is None:
        problem = _sandbox_problem()
        if isinstance(problem, ImportError):
            logger.info("Stream-Mapparr: apps.plugins.sandbox not available; using legacy /data paths")
            return _legacy(key, "sandbox module not importable")
        if problem is not None:
            failures.append("sandbox import raised %s" % type(problem).__name__)
        logger.error("Stream-Mapparr: data directory not resolved (%s); using legacy /data paths",
                     "; ".join(failures) or "no lookup returned a path")
        return _legacy(key, "no lookup returned a path")

    problem = _guard(data_dir, plugins_dir)
    if problem is None:
        try:
            os.makedirs(data_dir, exist_ok=True)
        except Exception as exc:
            problem = "makedirs failed (%s)" % type(exc).__name__
    if problem is not None:
        logger.error("Stream-Mapparr: resolved data directory rejected (%s); using legacy /data paths",
                     problem)
        return _legacy(key, problem)
    return Layout(False, data_dir, plugins_dir, key, reason)


def _legacy(key: str, reason: str) -> Layout:
    return Layout(True, None, None, key, reason)


def _sandbox_problem():
    """None when apps.plugins.sandbox imports, else the exception the import raised."""
    try:
        importlib.import_module("apps.plugins.sandbox")
    except Exception as exc:
        return exc
    return None


def _from_module_context(plugin_dir, failures):
    """Step 1: the loader's module context for this file, through the sandbox helper."""
    try:
        from apps.plugins.loader import _plugin_module_context
        from apps.plugins.sandbox import stable_plugin_data_path
    except ImportError:
        return None, None
    except Exception as exc:
        failures.append("step 1 import raised %s" % type(exc).__name__)
        return None, None
    try:
        ctx = _plugin_module_context(os.path.join(plugin_dir, "plugin.py"))
    except Exception as exc:
        failures.append("module context raised %s" % type(exc).__name__)
        return None, None
    if not isinstance(ctx, tuple) or len(ctx) < 3 or not all(isinstance(v, str) for v in ctx[:3]):
        failures.append("module context returned no usable tuple")
        return None, None
    try:
        data = stable_plugin_data_path(ctx[0], ctx[1], ctx[2])
    except Exception as exc:
        failures.append("stable_plugin_data_path raised %s" % type(exc).__name__)
        return None, None
    if not isinstance(data, str) or not data:
        failures.append("stable_plugin_data_path returned no path")
        return None, None
    return data, "module context"


def _from_registry(key, failures):
    """Step 2: the data_dir the loader recorded for this key (needs no sandbox module)."""
    try:
        from apps.plugins.loader import PluginManager
    except ImportError:
        return None, None
    try:
        entry = PluginManager.get()._registry.get(key)
    except Exception as exc:
        failures.append("registry lookup raised %s" % type(exc).__name__)
        return None, None
    data = getattr(entry, "data_dir", None)
    if not isinstance(data, str) or not data:
        failures.append("registry has no data_dir for the key")
        return None, None
    return data, "registry"


def _guard(data_dir: str, plugins_dir: str) -> str | None:
    """Return why data_dir is unsafe, or None when it may be used."""
    if not os.path.isabs(data_dir):
        return "path is not absolute"
    root = os.path.join(plugins_dir, "_data")
    parent = os.path.dirname(os.path.abspath(data_dir))
    if os.path.basename(os.path.dirname(data_dir)) != "_data" or \
            os.path.normcase(parent) != os.path.normcase(root):
        return "path is not directly under <plugins>/_data"
    if os.path.islink(data_dir) or os.path.islink(root):
        return "path or its root is a symlink"
    return None


# ---------------------------------------------------------------------------
# Carry-over of the old /data state (storage.migrate, storage.sync_tally_tail)
# ---------------------------------------------------------------------------

_STATE_NAME = "_stream_mapparr_storage_state"
_OFFSETS = "match_counts.offsets.json"
_TAIL_LOCK = "match_counts.lock"
_MIGRATE_LOCK = ".migrate.lock"


@dataclass(frozen=True)
class Item:
    """One move from an old path to a new one.

    kind is "file", "dir", "nocopy", "tally", "usage_dir" or "beside". rel is the path
    relative to the data directory (None for "beside" and the shared reports dir).
    """

    name: str
    old: str
    new: str
    kind: str
    rel: str | None = None


@dataclass
class MigrateResult:
    copied: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    install_id_blocked: bool = False
    skipped_pass: bool = False


def _state():
    """The parked process state: a module with no __file__ (bug-136)."""
    mod = sys.modules.get(_STATE_NAME)
    if mod is None:
        mod = types.ModuleType(_STATE_NAME)
        sys.modules[_STATE_NAME] = mod
    if not hasattr(mod, "migrated_dirs"):
        mod.migrated_dirs = set()
    return mod


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def _refuse_real(path):
    """Raise when a path would touch Dispatcharr's real /data (tests set REFUSE_REAL_DATA)."""
    if not REFUSE_REAL_DATA or not path:
        return
    p = str(path).replace("\\", "/")
    if p == "/data" or p.startswith("/data/"):
        raise RuntimeError("storage refuses to touch real Dispatcharr data: %s" % p)


@contextlib.contextmanager
def _lock(path):
    """Exclusive flock on path, retried with a short sleep that yields the gevent hub."""
    if fcntl is None:
        yield
        return
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.01)
        yield
    finally:
        os.close(fd)


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


def _append_bytes(path, data):
    with open(path, "ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _temp_path(path):
    head, tail = os.path.split(path)
    return os.path.join(head, ".%s.tmp-%d" % (tail, os.getpid()))


def atomic_write_bytes(path, data):
    """Write data to path through a dot-prefixed temp file, fsync and os.replace."""
    head = os.path.dirname(path)
    if head:
        os.makedirs(head, exist_ok=True)
    tmp = _temp_path(path)
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj):
    """atomic_write_bytes of obj as sorted, indented ASCII JSON."""
    atomic_write_bytes(path, (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("ascii"))


def _load_offsets(path):
    if not os.path.exists(path):
        return {}
    obj = json.loads(_read_bytes(path).decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("offsets file is not an object")
    return {str(k): int(v) for k, v in obj.items()}


def _previous_dir(layout, logger):
    """Source (a): the data dir of the last finished pass, or None.

    A corrupt pointer is logged once and treated as absent, so the folder-key rule applies
    and the pass can still complete and rewrite a valid pointer.
    """
    active = layout.beside(".active")
    prev = os.path.join(layout.plugins_dir, "_data", layout.key)
    if os.path.exists(active):
        try:
            prev = json.loads(_read_bytes(active).decode("utf-8")).get("data_dir")
        except Exception as exc:
            logger.error("Stream-Mapparr: %s unreadable (%s); using the folder-key directory"
                         % (os.path.basename(active), type(exc).__name__))
            prev = os.path.join(layout.plugins_dir, "_data", layout.key)
    if not isinstance(prev, str) or not prev:
        return None
    if _norm(prev) == _norm(layout.data_dir) or not os.path.isdir(prev):
        return None
    return prev


def _sources(item, prev, pass_b, use_a):
    out = []
    if use_a and prev and item.rel:
        out.append(os.path.join(prev, item.rel))
    if pass_b and item.old:
        out.append(item.old)
    return out


def _copy_one(dst, sources, copied):
    """Copy the first existing source to dst, unless dst already exists (new wins)."""
    if os.path.exists(dst):
        return
    for src in sources:
        if os.path.isfile(src):
            atomic_write_bytes(dst, _read_bytes(src))
            copied.append((dst, src))
            return


def _copy_tally(item, data_dir, sources, copied):
    """Copy the tally cut at its last newline, recording the offset before the copy shows."""
    if os.path.exists(item.new):
        return
    for src in sources:
        if os.path.isfile(src):
            data = _read_bytes(src)
            cut = data[: data.rfind(b"\n") + 1]
            offsets_path = os.path.join(data_dir, _OFFSETS)
            # The tail takes the same lock, so it never sees the offset without the tally,
            # or the tally without its offset.
            with _lock(os.path.join(data_dir, _TAIL_LOCK)):
                offsets = _load_offsets(offsets_path)
                offsets[src] = len(cut)
                atomic_write_json(offsets_path, offsets)
                atomic_write_bytes(item.new, cut)
            copied.append((item.new, src))
            return


def _copy_dir(roots, dst_base, failed, copied, logger):
    """Copy every file under the source roots that dst_base lacks. True if install_id failed."""
    blocked = False
    seen = set()
    for root in roots:
        if not os.path.isdir(root):
            continue
        for here, _dirs, names in os.walk(root):
            for name in sorted(names):
                full = os.path.join(here, name)
                rel = os.path.relpath(full, root)
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    _copy_one(os.path.join(dst_base, rel), [full], copied)
                except Exception as exc:
                    logger.error("Stream-Mapparr: could not copy %s (%s)" % (name, type(exc).__name__))
                    failed.append(name)
                    if name == "install_id":
                        blocked = True
    return blocked


def _skipped():
    return MigrateResult([], [], False, True)


def migrate(layout, items, logger, version=None):
    """Copy the old state into the data dir once. Design: the Carry-over section."""
    if layout.legacy:
        return _skipped()
    flags = _state().migrated_dirs
    if _norm(layout.data_dir) in flags:
        return _skipped()
    _refuse_real(layout.data_dir)
    for item in items:
        _refuse_real(item.old)
        _refuse_real(item.new)
    os.makedirs(layout.data_dir, exist_ok=True)
    with _lock(os.path.join(layout.data_dir, _MIGRATE_LOCK)):
        if _norm(layout.data_dir) in flags:
            return _skipped()
        failed = []
        copied = []
        blocked = False
        prev = _previous_dir(layout, logger)
        _refuse_real(prev)
        pass_b = not os.path.exists(layout.beside(".migrated"))
        for item in items:
            if item.kind == "nocopy":
                continue
            try:
                if item.kind == "file":
                    _copy_one(item.new, _sources(item, prev, pass_b, True), copied)
                elif item.kind == "beside":
                    _copy_one(item.new, _sources(item, None, pass_b, False), copied)
                elif item.kind == "tally":
                    _copy_tally(item, layout.data_dir, _sources(item, prev, pass_b, True), copied)
                elif item.kind in ("dir", "usage_dir"):
                    sub = ("stream-mapparr",) if item.kind == "usage_dir" else ()
                    roots = []
                    if prev and item.rel:
                        roots.append(os.path.join(prev, item.rel, *sub))
                    if pass_b and item.old:
                        roots.append(os.path.join(item.old, *sub))
                    if _copy_dir(roots, os.path.join(item.new, *sub), failed, copied, logger):
                        blocked = True
                else:
                    raise ValueError("unknown item kind %r" % item.kind)
            except Exception as exc:
                label = os.path.basename(item.new.rstrip("/\\")) or item.name
                logger.error("Stream-Mapparr: could not carry over %s (%s)" % (label, type(exc).__name__))
                failed.append(label)
        if not blocked:
            flags.add(_norm(layout.data_dir))
        now = time.time()
        try:
            atomic_write_json(os.path.join(layout.data_dir, "migrated.json"), {
                "version": version,
                "time": now,
                "copied": [{"path": dst, "source": src} for dst, src in copied],
                "failed": failed,
            })
        except Exception as exc:
            logger.error("Stream-Mapparr: migrated.json not written (%s)" % type(exc).__name__)
        if not failed:
            try:
                atomic_write_json(layout.beside(".migrated"), {"time": now})
                atomic_write_json(layout.beside(".active"), {"data_dir": layout.data_dir})
            except Exception as exc:
                logger.error("Stream-Mapparr: carry-over marker not written (%s); the next start retries"
                             % type(exc).__name__)
        logger.info("Stream-Mapparr: carry-over copied %d file(s), %d failed" % (len(copied), len(failed)))
    return MigrateResult([dst for dst, _src in copied], failed, blocked, False)


def sync_tally_tail(layout, tally_path, logger):
    """Append to the tally the bytes its old source gained since the copy. Returns bytes appended.

    The offset is written before the append, so a crash between the two loses that batch
    from the count (an undercount) and never counts a line twice.
    """
    if layout.legacy:
        return 0
    _refuse_real(tally_path)
    _refuse_real(layout.data_dir)
    offsets_path = os.path.join(layout.data_dir, _OFFSETS)
    if not os.path.exists(offsets_path) or not os.path.exists(tally_path):
        return 0
    appended = 0
    with _lock(os.path.join(layout.data_dir, _TAIL_LOCK)):
        try:
            offsets = _load_offsets(offsets_path)
        except Exception as exc:
            logger.error("Stream-Mapparr: tally offsets unreadable (%s); no tail appended"
                         % type(exc).__name__)
            return 0
        for src in sorted(offsets):
            _refuse_real(src)
            if not os.path.isfile(src):
                continue
            off = offsets[src]
            try:
                data = _read_bytes(src)
            except Exception as exc:
                logger.error("Stream-Mapparr: tally source unreadable (%s)" % type(exc).__name__)
                continue
            chunk = data[off:]
            cut = chunk[: chunk.rfind(b"\n") + 1]
            if not cut:
                continue
            trial = dict(offsets)
            trial[src] = off + len(cut)
            try:
                atomic_write_json(offsets_path, trial)
            except Exception as exc:
                logger.error("Stream-Mapparr: tally offset not written (%s); batch skipped"
                             % type(exc).__name__)
                continue
            offsets[src] = off + len(cut)
            try:
                _append_bytes(tally_path, cut)
            except Exception as exc:
                logger.error("Stream-Mapparr: tally append failed (%s); batch lost from the count"
                             % type(exc).__name__)
                continue
            appended += len(cut)
    return appended


# ---------------------------------------------------------------------------
# Binding the state paths at import (storage.bind, storage.items_for)
# ---------------------------------------------------------------------------

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
    ("OPERATION_LOCK_FILE", "nocopy", "operation.lock"),
    ("M3U_REFRESH_LOCK_FILE", "nocopy", "m3u_refresh.lock"),
    ("M3U_REFRESH_PENDING_FILE", "nocopy", "m3u_refresh_pending"),
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
}


def items_for(cfg, layout):
    """The moves bind hands to migrate. Each old path is cfg.LEGACY_PATHS[name]."""
    out = []
    for name, kind, rel in _BIND_TABLE:
        if name == "REPORT_DIR":
            new = layout.shared_reports_dir()
        elif name in _BESIDE:
            new = layout.beside(_BESIDE[name])
        else:
            new = os.path.join(layout.data_dir, rel)
        out.append(Item(name, cfg.LEGACY_PATHS[name], new, kind, rel))
    return out


def bind(cfg, plugin_file, logger):
    """Resolve the data dir, rebind cfg's state constants to it and carry the old state over.

    Runs once per module import. Any failure in migrate puts every constant back to its
    legacy literal and returns a legacy Layout, so the process runs exactly as before.
    """
    legacy_paths = {name: getattr(cfg, name) for name, _kind, _rel in _BIND_TABLE}
    cfg.LEGACY_PATHS = dict(legacy_paths)
    cfg.STORAGE_MIGRATION = None
    layout = resolve(os.path.dirname(os.path.abspath(plugin_file)), cfg.PLUGIN_DB_KEY, logger)
    cfg.STORAGE_LAYOUT = layout
    if layout.legacy:
        return layout
    items = items_for(cfg, layout)
    try:
        for item in items:
            setattr(cfg, item.name, item.new)
        cfg.STORAGE_MIGRATION = migrate(layout, items, logger, version=cfg.PLUGIN_VERSION)
    except Exception as exc:
        for name, value in legacy_paths.items():
            setattr(cfg, name, value)
        cfg.STORAGE_MIGRATION = None
        cfg.STORAGE_LAYOUT = Layout(True, None, None, cfg.PLUGIN_DB_KEY, "migrate failed")
        logger.error("Stream-Mapparr: carry-over failed (%s); using legacy /data paths"
                     % type(exc).__name__)
    return cfg.STORAGE_LAYOUT
