"""Readouts fall back to /data when /config cannot be written.

Dispatcharr's plugin guide (Plugins.md, read 2026-10-17) asks plugins to write
only under /data or /app/data, and a stock Dispatcharr image has no writable
/config. This installation bind-mounts /config to the host, which is why the
readouts went there. _readout_dir keeps that directory when it can be written
and otherwise uses PluginConfig.READOUT_FALLBACK_DIR, so the actions that write
a readout work on a stock installation too.
"""
import logging


def _bare(plugin_module):
    return plugin_module.Plugin.__new__(plugin_module.Plugin)


def test_the_config_directory_is_used_when_it_is_writable(plugin_module, tmp_path, monkeypatch):
    plugin = _bare(plugin_module)
    plugin.BUG_REPORT_DIR = str(tmp_path / "config" / "stream-mapparr")
    assert plugin._readout_dir() == plugin.BUG_REPORT_DIR


def test_the_data_fallback_is_used_when_config_cannot_be_written(plugin_module, tmp_path, monkeypatch):
    plugin = _bare(plugin_module)
    plugin.BUG_REPORT_DIR = "/nonexistent\x00dir"
    fallback = plugin_module.PluginConfig.READOUT_FALLBACK_DIR
    assert str(tmp_path) in fallback, "the conftest sandbox must redirect the /data fallback"
    assert plugin._readout_dir() == fallback


def test_the_fallback_is_a_data_path(plugin_module):
    """The sandboxing in tests/conftest.py rewrites /data paths, so read the source value."""
    import inspect
    source = inspect.getsource(plugin_module.PluginConfig)
    assert 'READOUT_FALLBACK_DIR = "/data/stream_mapparr_readouts"' in source


def test_a_readout_action_writes_to_the_fallback(plugin_module, tmp_path, monkeypatch):
    plugin = _bare(plugin_module)
    plugin.version = "1.26.9999999"
    monkeypatch.setattr(plugin_module.Plugin, "BUG_REPORT_DIR", "/nonexistent\x00dir", raising=False)
    result = plugin.report_a_bug_action({}, logging.getLogger("t"))
    assert result["status"] != "error", result
    assert result.get("file", "").startswith(plugin_module.PluginConfig.READOUT_FALLBACK_DIR), result
