"""Which IPTV Checker progress file Stream-Mapparr reads.

IPTV Checker moves its progress file to /data/plugins/_data/iptv_checker.progress.json
(the hyphen slug /data/plugins/_data/iptv-checker.progress.json after a Hub install) and
keeps writing the legacy /data/iptv_checker_progress.json during the rollout. The helper
_iptv_checker_progress_path() must pick the existing file with the newest mtime.

Every path here is redirected into tmp_path. The autouse sandbox in conftest.py handles
the tuple of candidates; the tests below also set the candidates explicitly.
"""
import logging
import os

log = logging.getLogger("t")

RUNNING = '{"status": "running", "current": 3, "total": 9}'
IDLE = '{"status": "idle"}'


def _plugin(plugin_module):
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.version = "test"
    return p


def _paths(plugin_module, monkeypatch, tmp_path):
    cfg = plugin_module.PluginConfig
    new = tmp_path / "plugins_data" / "iptv_checker.progress.json"
    hyphen = tmp_path / "plugins_data" / "iptv-checker.progress.json"
    legacy = tmp_path / "iptv_checker_progress.json"
    new.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cfg, "IPTV_CHECKER_PROGRESS_CANDIDATES", (str(new), str(hyphen)))
    monkeypatch.setattr(cfg, "IPTV_CHECKER_PROGRESS_FILE", str(legacy))
    return new, hyphen, legacy


def _write(path, content, mtime):
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_new_path_only_is_read(plugin_module, monkeypatch, tmp_path):
    new, _, legacy = _paths(plugin_module, monkeypatch, tmp_path)
    _write(new, RUNNING, 1_000_000)
    assert plugin_module._iptv_checker_progress_path() == str(new)
    assert _plugin(plugin_module)._iptv_checker_is_running(log) is True


def test_old_path_only_is_read(plugin_module, monkeypatch, tmp_path):
    _, _, legacy = _paths(plugin_module, monkeypatch, tmp_path)
    _write(legacy, RUNNING, 1_000_000)
    assert plugin_module._iptv_checker_progress_path() == str(legacy)
    assert _plugin(plugin_module)._iptv_checker_is_running(log) is True


def test_hyphen_slug_path_is_read(plugin_module, monkeypatch, tmp_path):
    _, hyphen, _ = _paths(plugin_module, monkeypatch, tmp_path)
    _write(hyphen, RUNNING, 1_000_000)
    assert plugin_module._iptv_checker_progress_path() == str(hyphen)
    assert _plugin(plugin_module)._iptv_checker_is_running(log) is True


def test_both_present_newest_mtime_wins_when_new_is_newer(plugin_module, monkeypatch, tmp_path):
    new, _, legacy = _paths(plugin_module, monkeypatch, tmp_path)
    _write(legacy, IDLE, 1_000_000)
    _write(new, RUNNING, 2_000_000)
    assert plugin_module._iptv_checker_progress_path() == str(new)
    assert _plugin(plugin_module)._iptv_checker_is_running(log) is True


def test_both_present_newest_mtime_wins_when_old_is_newer(plugin_module, monkeypatch, tmp_path):
    new, _, legacy = _paths(plugin_module, monkeypatch, tmp_path)
    _write(new, RUNNING, 1_000_000)
    _write(legacy, IDLE, 2_000_000)
    assert plugin_module._iptv_checker_progress_path() == str(legacy)
    assert _plugin(plugin_module)._iptv_checker_is_running(log) is False


def test_none_present_behaves_as_before(plugin_module, monkeypatch, tmp_path):
    """No file anywhere: the legacy path comes back, so the wait sees a missing file
    (returns True at once) and the stand-aside check is False, as before the helper."""
    _, _, legacy = _paths(plugin_module, monkeypatch, tmp_path)
    assert plugin_module._iptv_checker_progress_path() == str(legacy)
    p = _plugin(plugin_module)
    assert p._iptv_checker_is_running(log) is False
    settings = {"wait_for_iptv_checker": True, "iptv_checker_max_wait_hours": 0}
    assert p._wait_for_iptv_checker_completion(settings, log) is True


def test_wait_site_reads_the_new_path(plugin_module, monkeypatch, tmp_path):
    """Running on the new path only, with a zero wait: the wait gives up at once and
    returns False. If the helper were broken (legacy path, missing) it would return True."""
    new, _, _ = _paths(plugin_module, monkeypatch, tmp_path)
    _write(new, RUNNING, 1_000_000)
    settings = {"wait_for_iptv_checker": True, "iptv_checker_max_wait_hours": 0}
    assert _plugin(plugin_module)._wait_for_iptv_checker_completion(settings, log) is False


def test_wait_site_reads_the_hyphen_path(plugin_module, monkeypatch, tmp_path):
    _, hyphen, _ = _paths(plugin_module, monkeypatch, tmp_path)
    _write(hyphen, RUNNING, 1_000_000)
    settings = {"wait_for_iptv_checker": True, "iptv_checker_max_wait_hours": 0}
    assert _plugin(plugin_module)._wait_for_iptv_checker_completion(settings, log) is False


def test_wait_site_newest_idle_file_wins(plugin_module, monkeypatch, tmp_path):
    new, _, legacy = _paths(plugin_module, monkeypatch, tmp_path)
    _write(new, RUNNING, 1_000_000)
    _write(legacy, IDLE, 2_000_000)
    settings = {"wait_for_iptv_checker": True, "iptv_checker_max_wait_hours": 0}
    assert _plugin(plugin_module)._wait_for_iptv_checker_completion(settings, log) is True
