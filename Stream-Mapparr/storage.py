"""Resolve the plugin's private data directory under Dispatcharr's plugin data root.

Dispatcharr 0.34 gives each plugin a directory at <plugins>/_data/<storage key>. This
module finds that directory and checks it. It stays stdlib-only and imports nothing from
plugin.py, and every Dispatcharr import happens inside a function so the module loads
(and its tests run) without Dispatcharr present.

When the directory cannot be trusted, resolve returns a legacy Layout: the caller keeps
using the old /data paths for that process.
"""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass


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
