"""storage.resolve picks the plugin data directory and falls back to legacy mode.

Dispatcharr is stubbed in sys.modules per test. The sandbox stub copies the body of
apps.plugins.sandbox.stable_plugin_data_path from Dispatcharr 0.34.1.
"""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from conftest import _PKG, _load_from_path


@pytest.fixture
def storage():
    name = f"{_PKG}.storage"
    if name not in sys.modules:
        _load_from_path(name, "storage.py")
    return sys.modules[name]


def _stable_plugin_data_path(plugin_key, plugin_path, storage_key=""):
    return os.path.join(os.path.dirname(os.path.abspath(plugin_path)), "_data", storage_key or plugin_key)


def _install(monkeypatch, *, ctx=None, registry=None, loader=True, sandbox=True,
             stable=_stable_plugin_data_path, loader_has_context=True):
    apps = types.ModuleType("apps")
    apps.__path__ = []
    plugins = types.ModuleType("apps.plugins")
    plugins.__path__ = []
    monkeypatch.setitem(sys.modules, "apps", apps)
    monkeypatch.setitem(sys.modules, "apps.plugins", plugins)
    monkeypatch.delitem(sys.modules, "apps.plugins.loader", raising=False)
    monkeypatch.delitem(sys.modules, "apps.plugins.sandbox", raising=False)
    if loader:
        mod = types.ModuleType("apps.plugins.loader")
        if loader_has_context:
            mod._plugin_module_context = lambda path: ctx
        manager = SimpleNamespace(_registry=registry if registry is not None else {})
        mod.PluginManager = SimpleNamespace(get=lambda: manager)
        monkeypatch.setitem(sys.modules, "apps.plugins.loader", mod)
    if sandbox:
        mod = types.ModuleType("apps.plugins.sandbox")
        mod.stable_plugin_data_path = stable
        monkeypatch.setitem(sys.modules, "apps.plugins.sandbox", mod)


def _dirs(tmp_path):
    plugins = tmp_path / "plugins"
    plugin_dir = plugins / "stream_mapparr"
    plugin_dir.mkdir(parents=True)
    return str(plugins), str(plugin_dir)


def test_step1_uses_module_context_storage_key(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "hubslug", (), 1))
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.data_dir == os.path.join(plugins, "_data", "hubslug")
    assert layout.legacy is False
    assert layout.plugins_dir == plugins
    assert os.path.isdir(layout.data_dir)
    logger.error.assert_not_called()


def test_step2_registry_when_no_module_context(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    expected = os.path.join(plugins, "_data", "stream-mapparr")
    _install(monkeypatch, ctx=None,
             registry={"stream-mapparr": SimpleNamespace(data_dir=expected)})

    layout = storage.resolve(plugin_dir, "stream-mapparr", MagicMock())

    assert layout.data_dir == expected
    assert layout.legacy is False


def test_no_sandbox_module_is_legacy_info(storage, monkeypatch, tmp_path):
    _, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, sandbox=False)
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert layout.data_dir is None
    assert logger.info.call_count == 1
    logger.error.assert_not_called()


def test_sandbox_present_but_lookups_fail_is_legacy_error(storage, monkeypatch, tmp_path):
    _, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, ctx=None, registry={})
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert logger.error.call_count == 1
    assert "registry" in logger.error.call_args[0][0] % logger.error.call_args[0][1:]


def test_guard_rejects_path_outside_data_root(storage, monkeypatch, tmp_path):
    _, plugin_dir = _dirs(tmp_path)
    elsewhere = str(tmp_path / "elsewhere")
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "x", (), 1),
             stable=lambda plugin_key, plugin_path, storage_key="": elsewhere)
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert logger.error.call_count == 1
    assert not os.path.exists(elsewhere)


def test_guard_rejects_symlink(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    data_root = os.path.join(plugins, "_data")
    os.makedirs(data_root)
    target = tmp_path / "target"
    target.mkdir()
    link = os.path.join(data_root, "stream-mapparr")
    try:
        os.symlink(str(target), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation needs privilege on this platform")
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "", (), 1))
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert logger.error.call_count == 1


def test_makedirs_failure_is_legacy(storage, monkeypatch, tmp_path):
    _, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "", (), 1))

    def refuse(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(storage.os, "makedirs", refuse)
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert logger.error.call_count == 1


def test_makedirs_non_oserror_is_legacy(storage, monkeypatch, tmp_path):
    _, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "", (), 1))

    def refuse(*args, **kwargs):
        raise ValueError("not a path")

    monkeypatch.setattr(storage.os, "makedirs", refuse)
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert logger.error.call_count == 1


def test_registry_used_when_sandbox_not_importable(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    expected = os.path.join(plugins, "_data", "stream-mapparr")
    _install(monkeypatch, sandbox=False, ctx=None,
             registry={"stream-mapparr": SimpleNamespace(data_dir=expected)})
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is False
    assert layout.data_dir == expected
    logger.error.assert_not_called()


def test_magicmock_values_are_rejected(storage, monkeypatch, tmp_path):
    _, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, ctx=MagicMock(),
             registry={"stream-mapparr": SimpleNamespace(data_dir=MagicMock())})
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert layout.data_dir is None


def test_guard_rejects_nested_path(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    nested = os.path.join(plugins, "_data", "a", "b")
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "x", (), 1),
             stable=lambda plugin_key, plugin_path, storage_key="": nested)
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.legacy is True
    assert logger.error.call_count == 1
    assert not os.path.exists(nested)


def test_step1_import_error_falls_through_silently(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    expected = os.path.join(plugins, "_data", "stream-mapparr")
    _install(monkeypatch, loader_has_context=False,
             registry={"stream-mapparr": SimpleNamespace(data_dir=expected)})
    logger = MagicMock()

    layout = storage.resolve(plugin_dir, "stream-mapparr", logger)

    assert layout.data_dir == expected
    assert layout.legacy is False
    logger.error.assert_not_called()


def test_beside_and_shared_reports_dir_hang_off_plugins_dir(storage, monkeypatch, tmp_path):
    plugins, plugin_dir = _dirs(tmp_path)
    _install(monkeypatch, ctx=("stream-mapparr", plugin_dir, "hubslug", (), 1))

    layout = storage.resolve(plugin_dir, "stream-mapparr", MagicMock())

    assert layout.beside(".active") == os.path.join(plugins, "_data", "stream-mapparr.active")
    assert layout.shared_reports_dir() == os.path.join(
        plugins, "_shared", "newsflasharr", "stream-mapparr")


# --------------------------------------------------------------------------- #
# Context cross-check: check_context compares Dispatcharr's data_dir with ours.
# --------------------------------------------------------------------------- #

def _bound_layout(storage, tmp_path):
    data_dir = str(tmp_path / "plugins" / "_data" / "stream-mapparr")
    return storage.Layout(False, data_dir, str(tmp_path / "plugins"), "stream-mapparr", "")


def test_context_equal_is_silent(storage, tmp_path):
    layout = _bound_layout(storage, tmp_path)
    logger = MagicMock()

    assert storage.check_context(layout, {"data_dir": layout.data_dir}, logger) is True
    logger.warning.assert_not_called()


def test_context_equal_after_normalising_is_silent(storage, tmp_path):
    layout = _bound_layout(storage, tmp_path)
    spelled = os.path.join(layout.data_dir, "..", "stream-mapparr")
    logger = MagicMock()

    assert storage.check_context(layout, {"data_dir": spelled}, logger) is True
    logger.warning.assert_not_called()


def test_context_missing_or_not_dict_is_silent(storage, tmp_path):
    layout = _bound_layout(storage, tmp_path)
    logger = MagicMock()

    for context in (None, "text", ["x"], {}, {"data_dir": None}, {"data_dir": 7}):
        assert storage.check_context(layout, context, logger) is True
    logger.warning.assert_not_called()


def test_context_mismatch_warns_once(storage, tmp_path):
    layout = _bound_layout(storage, tmp_path)
    given = str(tmp_path / "plugins" / "_data" / "stream-mapparr-hub")
    logger = MagicMock()

    assert storage.check_context(layout, {"data_dir": given}, logger) is True
    assert storage.check_context(layout, {"data_dir": given}, logger) is True

    assert logger.warning.call_count == 1
    message = logger.warning.call_args[0][0]
    assert layout.data_dir in message
    assert os.path.abspath(given) in message
    assert "Hub install" in message
    assert "Plugins page refresh" in message


def test_context_mismatch_warns_again_for_a_new_pair(storage, tmp_path):
    layout = _bound_layout(storage, tmp_path)
    logger = MagicMock()

    storage.check_context(layout, {"data_dir": str(tmp_path / "one")}, logger)
    storage.check_context(layout, {"data_dir": str(tmp_path / "two")}, logger)

    assert logger.warning.call_count == 2


def test_legacy_layout_never_warns(storage, tmp_path):
    layout = storage.Layout(True, None, None, "stream-mapparr", "no plugin dir")
    logger = MagicMock()

    assert storage.check_context(layout, {"data_dir": str(tmp_path / "x")}, logger) is True
    logger.warning.assert_not_called()


def test_run_calls_check_before_event_branches(plugin_module, monkeypatch):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.version = "test"
    order = []
    ctx = {"data_dir": "/x"}

    def fake_check(layout, context, logger):
        order.append(("check", context))
        return True

    def fake_handler(settings, logger, context):
        order.append(("handler", context))
        return {}

    monkeypatch.setattr(plugin_module.storage, "check_context", fake_check)
    monkeypatch.setattr(p, "on_iptv_checker_scan_action", fake_handler)

    assert p.run("on_iptv_checker_scan", {}, ctx) == {}
    assert order == [("check", ctx), ("handler", ctx)]


def test_run_survives_a_check_that_raises(plugin_module, monkeypatch):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.version = "test"

    def boom(layout, context, logger):
        raise RuntimeError("check exploded")

    monkeypatch.setattr(plugin_module.storage, "check_context", boom)
    monkeypatch.setattr(p, "on_iptv_checker_scan_action", lambda s, l, c: {"ran": True})

    assert p.run("on_iptv_checker_scan", {}, {"data_dir": "/x"}) == {"ran": True}
