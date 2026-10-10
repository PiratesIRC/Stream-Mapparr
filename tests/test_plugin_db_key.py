"""The plugin row key is the key Dispatcharr gave THIS install, not a fixed string.

Dispatcharr keys a plugin by the folder it is installed in. A hand-copied folder
keeps its name lowercased, so it is keyed "stream-mapparr". The Plugin Hub
installer sanitises the folder name and writes underscores, so a Hub install is
keyed "stream_mapparr". A key written out as "stream-mapparr" finds no row on a
Hub install, the saved settings read as empty, and the database-wins schedule
repair never runs.
"""
import os
import pathlib
import sys
import types


def _key_for(plugin_module):
    return plugin_module._plugin_key_for_dir(os.path.dirname(plugin_module.__file__))


def test_a_hub_install_folder_gives_the_underscore_key(plugin_module):
    assert plugin_module._plugin_key_for_dir("/data/plugins/stream_mapparr") == "stream_mapparr"


def test_a_hand_copied_folder_keeps_its_hyphen(plugin_module):
    assert plugin_module._plugin_key_for_dir("/data/plugins/stream-mapparr") == "stream-mapparr"


def test_case_and_spaces_follow_the_loader(plugin_module):
    assert plugin_module._plugin_key_for_dir("/data/plugins/Stream Mapparr") == "stream_mapparr"
    assert plugin_module._plugin_key_for_dir("/data/plugins/stream_mapparr/") == "stream_mapparr"


def test_the_key_comes_from_this_files_own_folder(plugin_module):
    assert plugin_module.PluginConfig.PLUGIN_DB_KEY == _key_for(plugin_module)


def test_the_saved_settings_are_read_under_that_key(plugin_module, monkeypatch):
    recorded = []
    saved = {"scheduled_times": "05:00"}

    class _Values:
        def __init__(self, key):
            self._key = key

        def values_list(self, field, flat=True):
            return self

        def first(self):
            return saved if self._key == plugin_module.PluginConfig.PLUGIN_DB_KEY else None

    class _Objects:
        def filter(self, key):
            recorded.append(key)
            return _Values(key)

    class _PluginConfig:
        objects = _Objects()

    models = types.ModuleType("apps.plugins.models")
    models.PluginConfig = _PluginConfig
    plugins = types.ModuleType("apps.plugins")
    plugins.__path__ = []
    monkeypatch.setitem(sys.modules, "apps.plugins", plugins)
    monkeypatch.setitem(sys.modules, "apps.plugins.models", models)

    plugin = plugin_module.Plugin.__new__(plugin_module.Plugin)
    assert plugin._settings_from_db() == saved
    assert recorded == [plugin_module.PluginConfig.PLUGIN_DB_KEY]


def test_the_key_is_computed_not_written_out(plugin_module):
    """Every checkout folder is named Stream-Mapparr, so a hard-coded value still
    passes the value tests above. Only the source text can show it was written out.
    """
    source = pathlib.Path(plugin_module.__file__).read_text(encoding="utf-8")
    lines = [line.strip() for line in source.splitlines() if line.strip().startswith("PLUGIN_DB_KEY =")]
    assert len(lines) == 1
    assert "_plugin_key_for_dir(" in lines[0]
