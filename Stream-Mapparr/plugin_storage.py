"""Plugin-independent storage layer: find a plugin's private data directory and carry old state over.

Dispatcharr 0.34 gives each plugin a directory at <plugins>/_data/<storage key>. This module
finds that directory, checks it, and moves the state a plugin wrote under the old /data root
into it once. It stays stdlib-only and imports nothing from any plugin, and every Dispatcharr
import happens inside a function, so the module loads (and its tests run) without Dispatcharr
present.

Everything that belongs to one plugin arrives as a Spec: the log prefix, the key of the parked
process state in sys.modules, the subdirectory names, the tally file names and the table of
state paths. The plugin's own storage.py builds one Spec and wraps each public function, passing
the Spec as the first argument.

When the directory cannot be trusted, resolve returns a legacy Layout: the caller keeps using
the old /data paths for that process.

VENDORING. A plugin cannot import from this directory at run time: it deploys as a self-contained
folder into /data/plugins, where this path does not exist. So this file is copied into each plugin
and pinned by hash, exactly as notify_client.py and matching_core.py are. Do not hand-edit a
vendored copy. Edit the shared source and re-vendor it with _shared/tools/sync_storage.py.
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

_MIGRATE_LOCK = ".migrate.lock"


@dataclass(frozen=True)
class Spec:
    """What makes one plugin's storage layer its own.

    label: the prefix of every log line, such as "Stream-Mapparr".
    state_name: the sys.modules key of the parked process state (bug-136). Unique per plugin.
    table: (constant name, kind, rel) rows. rel is the path under the data directory, or None for
        the rows named in beside and reports_constant.
    beside: constant name -> suffix of a file kept beside the data directories, in <plugins>/_data/.
    reports_subdir: folder under <plugins>/_shared/newsflasharr/ that holds the plugin's reports.
        Required when the table has a row named reports_constant; None otherwise.
    usage_subdir: folder inside a "usage_dir" row's directory. None means no extra folder.
    tally_offsets, tally_lock: file names of the match tally's offset record and its lock, both kept
        in the data directory. The defaults are the names Stream-Mapparr already uses on disk.
    reports_constant: the table row whose new path is the shared reports directory.
    """

    label: str
    state_name: str
    table: tuple
    beside: dict = field(default_factory=dict)
    reports_subdir: str | None = None
    usage_subdir: str | None = None
    tally_offsets: str = "match_counts.offsets.json"
    tally_lock: str = "match_counts.lock"
    reports_constant: str = "REPORT_DIR"


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

    def shared_reports_dir(self, subdir: str) -> str:
        """Directory Newsflasharr reads reports from, under <plugins>/_shared/newsflasharr/<subdir>."""
        return os.path.join(self.plugins_dir, "_shared", "newsflasharr", subdir)


def resolve(spec: Spec, plugin_dir: str, key: str, logger) -> Layout:
    """Resolve the data directory for the plugin folder plugin_dir.

    plugin_dir is the folder holding the plugin's storage module. key is the plugin's registry key
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
            logger.info("%s: apps.plugins.sandbox not available; using legacy /data paths" % spec.label)
            return _legacy(key, "sandbox module not importable")
        if problem is not None:
            failures.append("sandbox import raised %s" % type(problem).__name__)
        logger.error("%s: data directory not resolved (%s); using legacy /data paths",
                     spec.label, "; ".join(failures) or "no lookup returned a path")
        return _legacy(key, "no lookup returned a path")

    _refuse_real(data_dir)
    problem = _guard(data_dir, plugins_dir)
    if problem is None:
        try:
            os.makedirs(data_dir, mode=0o755, exist_ok=True)
        except Exception as exc:
            problem = "makedirs failed (%s)" % type(exc).__name__
    if problem is not None:
        logger.error("%s: resolved data directory rejected (%s); using legacy /data paths",
                     spec.label, problem)
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
# Carry-over of the old /data state (migrate, sync_tally_tail)
# ---------------------------------------------------------------------------


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


def _state(spec: Spec):
    """The parked process state: a module with no __file__ (bug-136)."""
    mod = sys.modules.get(spec.state_name)
    if mod is None:
        mod = types.ModuleType(spec.state_name)
        sys.modules[spec.state_name] = mod
    if not hasattr(mod, "migrated_dirs"):
        mod.migrated_dirs = set()
    if not hasattr(mod, "tally_sources"):
        # normalized tally path -> the legacy sources migrate was given for it (this process only)
        mod.tally_sources = {}
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
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
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
    _refuse_real(path)
    with open(path, "ab") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _temp_path(path):
    head, tail = os.path.split(path)
    return os.path.join(head, ".%s.tmp-%d" % (tail, os.getpid()))


def _fsync_dir(head):
    """fsync a directory so a rename inside it survives a crash. POSIX only; never raises."""
    if os.name != "posix" or not head:
        return
    try:
        fd = os.open(head, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except Exception:
        pass


def atomic_write_bytes(path, data):
    """Write data to path through a dot-prefixed temp file, fsync and os.replace.

    The temp file is owner-only (0o600), so state files are not readable by other users.
    """
    _refuse_real(path)
    head = os.path.dirname(path)
    if head:
        os.makedirs(head, mode=0o755, exist_ok=True)
    tmp = _temp_path(path)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
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
    _fsync_dir(head)


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


def _fallback_dir(layout):
    """The key-derived data dir, or None when the guard refuses it. It is a copy source, so it is guarded."""
    fallback = os.path.join(layout.plugins_dir, "_data", layout.key)
    return fallback if _guard(fallback, layout.plugins_dir) is None else None


def _previous_dir(spec: Spec, layout, logger):
    """Source (a): the data dir of the last finished pass, or None.

    A corrupt pointer is logged once and treated as absent, so the folder-key rule applies
    and the pass can still complete and rewrite a valid pointer.
    """
    active = layout.beside(".active")
    prev = None
    if os.path.exists(active):
        try:
            prev = json.loads(_read_bytes(active).decode("utf-8")).get("data_dir")
        except Exception as exc:
            logger.error("%s: %s unreadable (%s); using the folder-key directory"
                         % (spec.label, os.path.basename(active), type(exc).__name__))
            prev = _fallback_dir(layout)
    else:
        prev = _fallback_dir(layout)
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


def _restore_offsets(offsets_path, before):
    """Put the offsets file back as it was before a failed copy. Best effort."""
    try:
        if before:
            atomic_write_json(offsets_path, before)
        elif os.path.exists(offsets_path):
            os.remove(offsets_path)
    except Exception:
        pass


def _copy_tally(spec: Spec, item, data_dir, sources, copied, logger):
    """Copy the tally cut at its last newline, recording the offset before the copy shows.

    When the new tally already exists (a first pass failed here and the plugin wrote its own
    lines), the legacy source has no offset yet. Its whole file is then new to the count, so the
    offset is 0 and the tail appends it once.

    The one double count is accepted: the offsets file is deleted by hand after a successful copy.
    The copy then holds the legacy lines with no offset, so the tail appends the whole legacy file
    again. A hand deletion is rare, and the tally is a badge counter, not a ledger.
    """
    offsets_path = os.path.join(data_dir, spec.tally_offsets)
    if os.path.exists(item.new):
        for src in sources:
            if not os.path.isfile(src):
                continue
            with _lock(os.path.join(data_dir, spec.tally_lock)):
                offsets = _load_offsets(offsets_path)
                if src not in offsets:
                    offsets[src] = 0
                    atomic_write_json(offsets_path, offsets)
                    logger.info("%s: tally offset recorded at 0 for a legacy file the new tally "
                                "already holds" % spec.label)
            return
        return
    for src in sources:
        if os.path.isfile(src):
            data = _read_bytes(src)
            cut = data[: data.rfind(b"\n") + 1]
            # The tail takes the same lock, so it never sees the offset without the tally,
            # or the tally without its offset.
            with _lock(os.path.join(data_dir, spec.tally_lock)):
                before = _load_offsets(offsets_path)
                offsets = dict(before)
                offsets[src] = len(cut)
                atomic_write_json(offsets_path, offsets)
                try:
                    atomic_write_bytes(item.new, cut)
                except BaseException:
                    # The offset must not claim bytes the new tally does not hold.
                    _restore_offsets(offsets_path, before)
                    raise
            copied.append((item.new, src))
            return


def _copy_dir(spec: Spec, roots, dst_base, failed, copied, logger):
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
                    logger.error("%s: could not copy %s (%s)" % (spec.label, name, type(exc).__name__))
                    failed.append(name)
                    if name == "install_id":
                        blocked = True
    return blocked


def _skipped():
    return MigrateResult([], [], False, True)


def _markers_settled(layout):
    """True when a finished pass already left the markers, naming this data dir."""
    try:
        if not os.path.exists(layout.beside(".migrated")):
            return False
        active = json.loads(_read_bytes(layout.beside(".active")).decode("utf-8"))
        return _norm(active.get("data_dir", "")) == _norm(layout.data_dir)
    except Exception:
        return False


def migrate(spec: Spec, layout, items, logger, version=None):
    """Copy the old state into the data dir once. Design: the Carry-over section."""
    if layout.legacy:
        return _skipped()
    flags = _state(spec).migrated_dirs
    if _norm(layout.data_dir) in flags:
        return _skipped()
    _refuse_real(layout.data_dir)
    for item in items:
        _refuse_real(item.old)
        _refuse_real(item.new)
    os.makedirs(layout.data_dir, mode=0o755, exist_ok=True)
    with _lock(os.path.join(layout.data_dir, _MIGRATE_LOCK)):
        if _norm(layout.data_dir) in flags:
            return _skipped()
        failed = []
        copied = []
        blocked = False
        prev = _previous_dir(spec, layout, logger)
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
                    srcs = _sources(item, prev, pass_b, True)
                    # Recorded before the copy, so sync_tally_tail knows the legacy source even when
                    # this pass fails before it writes an offset (S2 a).
                    _state(spec).tally_sources[_norm(item.new)] = list(srcs)
                    _copy_tally(spec, item, layout.data_dir, srcs, copied, logger)
                elif item.kind in ("dir", "usage_dir"):
                    sub = (spec.usage_subdir,) if item.kind == "usage_dir" and spec.usage_subdir else ()
                    if item.rel is None:
                        # The shared reports dir is read by Newsflasharr, which needs it world-readable.
                        os.makedirs(item.new, mode=0o755, exist_ok=True)
                    roots = []
                    if prev and item.rel:
                        roots.append(os.path.join(prev, item.rel, *sub))
                    if pass_b and item.old:
                        roots.append(os.path.join(item.old, *sub))
                    if _copy_dir(spec, roots, os.path.join(item.new, *sub), failed, copied, logger):
                        blocked = True
                else:
                    raise ValueError("unknown item kind %r" % item.kind)
            except Exception as exc:
                label = os.path.basename(item.new.rstrip("/\\")) or item.name
                logger.error("%s: could not carry over %s (%s)" % (spec.label, label, type(exc).__name__))
                failed.append(label)
        if not blocked:
            flags.add(_norm(layout.data_dir))
        if not failed and not copied and _markers_settled(layout):
            # Every later process lands here: nothing to copy and the markers already name this dir.
            return MigrateResult([], [], blocked, False)
        now = time.time()
        try:
            atomic_write_json(os.path.join(layout.data_dir, "migrated.json"), {
                "version": version,
                "time": now,
                "copied": [{"path": dst, "source": src} for dst, src in copied],
                "failed": failed,
            })
        except Exception as exc:
            logger.error("%s: migrated.json not written (%s)" % (spec.label, type(exc).__name__))
        if not failed:
            try:
                atomic_write_json(layout.beside(".migrated"), {"time": now})
                atomic_write_json(layout.beside(".active"), {"data_dir": layout.data_dir})
            except Exception as exc:
                logger.error("%s: carry-over marker not written (%s); the next start retries"
                             % (spec.label, type(exc).__name__))
        logger.info("%s: carry-over copied %d file(s), %d failed" % (spec.label, len(copied), len(failed)))
    return MigrateResult([dst for dst, _src in copied], failed, blocked, False)


def sync_tally_tail(spec: Spec, layout, tally_path, logger):
    """Append to the tally the bytes its old source gained since the copy. Returns bytes appended.

    The offset is written before the append, so a crash between the two loses that batch
    from the count (an undercount) and never counts a line twice.
    """
    if layout.legacy:
        return 0
    _refuse_real(tally_path)
    _refuse_real(layout.data_dir)
    offsets_path = os.path.join(layout.data_dir, spec.tally_offsets)
    if not os.path.exists(offsets_path) or not os.path.exists(tally_path):
        return 0
    appended = 0
    with _lock(os.path.join(layout.data_dir, spec.tally_lock)):
        try:
            offsets = _load_offsets(offsets_path)
        except Exception as exc:
            logger.error("%s: tally offsets unreadable (%s); no tail appended"
                         % (spec.label, type(exc).__name__))
            return 0
        # A legacy source migrate named for this tally but that has no offset yet is read from 0 (S2 a).
        for src in _state(spec).tally_sources.get(_norm(tally_path), []):
            if os.path.isfile(src) and src not in offsets:
                offsets[src] = 0
                logger.info("%s: tally offset for a legacy file with no record read from 0"
                            % spec.label)
        for src in sorted(offsets):
            _refuse_real(src)
            if not os.path.isfile(src):
                continue
            off = offsets[src]
            try:
                size = os.path.getsize(src)
                rotated = size < off
                if rotated:
                    # Old code rotated the legacy file: read it again from the start (S2 c).
                    logger.info("%s: tally source is shorter than its offset (rotated); reading from 0"
                                % spec.label)
                    off = 0
                with open(src, "rb") as fh:
                    fh.seek(off)
                    data = fh.read()
            except Exception as exc:
                logger.error("%s: tally source unreadable (%s)" % (spec.label, type(exc).__name__))
                continue
            cut = data[: data.rfind(b"\n") + 1]
            if not cut and not rotated:
                continue
            trial = dict(offsets)
            trial[src] = off + len(cut)
            try:
                atomic_write_json(offsets_path, trial)
            except Exception as exc:
                logger.error("%s: tally offset not written (%s); batch skipped"
                             % (spec.label, type(exc).__name__))
                continue
            offsets[src] = off + len(cut)
            if not cut:
                continue
            try:
                _append_bytes(tally_path, cut)
            except Exception as exc:
                logger.error("%s: tally append failed (%s); batch lost from the count"
                             % (spec.label, type(exc).__name__))
                continue
            appended += len(cut)
    return appended


# ---------------------------------------------------------------------------
# Binding the state paths at import (bind, items_for)
# ---------------------------------------------------------------------------


def items_for(spec: Spec, cfg, layout):
    """The moves bind hands to migrate. Each old path is cfg.LEGACY_PATHS[name].

    Raises ValueError when the table has the reports row and the Spec names no reports_subdir.
    """
    out = []
    for name, kind, rel in spec.table:
        if name == spec.reports_constant:
            if spec.reports_subdir is None:
                raise ValueError("%s row needs a reports_subdir in the Spec" % name)
            new = layout.shared_reports_dir(spec.reports_subdir)
        elif name in spec.beside:
            new = layout.beside(spec.beside[name])
        else:
            new = os.path.join(layout.data_dir, rel)
        out.append(Item(name, cfg.LEGACY_PATHS[name], new, kind, rel))
    return out


def bind(spec: Spec, cfg, plugin_file, logger):
    """Resolve the data dir, rebind cfg's state constants to it and carry the old state over.

    Runs once per module import. Any exception, from resolve, items_for or migrate, puts every
    constant back to its legacy literal and returns a legacy Layout, so the process runs exactly
    as before and the plugin still imports.
    """
    legacy_paths = None
    stage = "bind"
    try:
        legacy_paths = {name: getattr(cfg, name) for name, _kind, _rel in spec.table}
        cfg.LEGACY_PATHS = dict(legacy_paths)
        cfg.STORAGE_MIGRATION = None
        layout = resolve(spec, os.path.dirname(os.path.abspath(plugin_file)), cfg.PLUGIN_DB_KEY, logger)
        cfg.STORAGE_LAYOUT = layout
        if layout.legacy:
            return layout
        items = items_for(spec, cfg, layout)
        for item in items:
            setattr(cfg, item.name, item.new)
        stage = "migrate"
        cfg.STORAGE_MIGRATION = migrate(spec, layout, items, logger, version=cfg.PLUGIN_VERSION)
    except Exception as exc:
        if legacy_paths is not None:
            for name, value in legacy_paths.items():
                setattr(cfg, name, value)
        cfg.STORAGE_MIGRATION = None
        reason = "migrate failed" if stage == "migrate" else "bind failed"
        cfg.STORAGE_LAYOUT = Layout(True, None, None, getattr(cfg, "PLUGIN_DB_KEY", ""), reason)
        logger.error("%s: carry-over failed (%s); using legacy /data paths"
                     % (spec.label, type(exc).__name__))
    return cfg.STORAGE_LAYOUT


def check_context(spec: Spec, layout, context, logger):
    """Compare Dispatcharr's context data_dir with the resolved one; warn once per pair.

    Returns True always. Equal paths, a context with no usable data_dir and a legacy layout are
    silent. A differing pair logs one WARNING per process: right after a Hub install changes the
    plugin's slug, the resolved directory and Dispatcharr's differ until the worker reloads. The
    process keeps the paths it resolved, so nothing here changes where state is read or written.
    """
    if getattr(layout, "legacy", True) or not isinstance(getattr(layout, "data_dir", None), str):
        return True
    if not isinstance(context, dict):
        return True
    given = context.get("data_dir")
    if not isinstance(given, str) or not given:
        return True
    resolved_norm = os.path.normcase(os.path.abspath(layout.data_dir))
    given_norm = os.path.normcase(os.path.abspath(given))
    if resolved_norm == given_norm:
        return True
    state = _state(spec)
    if not hasattr(state, "warned_contexts"):
        state.warned_contexts = set()
    pair = (resolved_norm, given_norm)
    if pair not in state.warned_contexts:
        state.warned_contexts.add(pair)
        logger.warning(
            "%s: Dispatcharr passed data_dir %s but this worker resolved %s. "
            "This is expected right after a Hub install that changes the plugin's slug; "
            "the worker moves to the new directory on its next reload (Plugins page "
            "refresh or container restart)." % (spec.label, given, layout.data_dir))
    return True
