"""Fixes to the shared storage layer (plugin_storage.py): tally offsets, guards, modes, owner-only writes.

Every path is a temp directory standing in for /data and <plugins>/_data. The process flag lives
in sys.modules, so an autouse fixture clears it around each test. A second migrate pass in the
same test stands for a new process, so it clears the flag first.
"""
import json
import os
import stat
import sys
from types import SimpleNamespace

import pytest

from conftest import _PKG, _load_from_path

STATE = "_stream_mapparr_storage_state"


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
    name = f"{_PKG}.storage"
    if name not in sys.modules:
        _load_from_path(name, "storage.py")
    return sys.modules[name]


@pytest.fixture(autouse=True)
def _clear_process_flag():
    sys.modules.pop(STATE, None)
    yield
    sys.modules.pop(STATE, None)


@pytest.fixture
def env(tmp_path):
    old = str(tmp_path / "data")
    plugins = str(tmp_path / "plugins")
    new = os.path.join(plugins, "_data", "stream-mapparr")
    os.makedirs(old, exist_ok=True)
    os.makedirs(os.path.join(plugins, "_data"))
    return SimpleNamespace(old=old, plugins=plugins, new=new, tmp=str(tmp_path))


def P(*parts):
    return os.path.join(*parts)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def layout_for(storage, env):
    return storage.Layout(False, env.new, env.plugins, "stream-mapparr", "test")


def legacy_tally(env):
    return P(env.old, "stream_mapparr_match_counts.jsonl")


def tally_path(env):
    return P(env.new, "match_counts.jsonl")


def offsets_path(env):
    return P(env.new, "match_counts.offsets.json")


def tally_item(storage, env):
    return storage.Item("TALLY", legacy_tally(env), tally_path(env), "tally", rel="match_counts.jsonl")


def fail_writes_to(storage, monkeypatch, target):
    """Make atomic_write_bytes raise for one path; every other write goes through."""
    real = storage._ps.atomic_write_bytes

    def write_or_fail(path, data):
        if path == target:
            raise OSError("injected failure for the test")
        return real(path, data)

    monkeypatch.setattr(storage._ps, "atomic_write_bytes", write_or_fail)
    return real


def test_tally_copy_after_failed_first_pass_counts_legacy_once(storage, env, monkeypatch):
    write(legacy_tally(env), "t1\nt2\n")
    lay = layout_for(storage, env)
    real = fail_writes_to(storage, monkeypatch, tally_path(env))
    first = storage.migrate(lay, [tally_item(storage, env)], Log())
    assert first.failed, "the first pass must fail on the tally copy"
    monkeypatch.setattr(storage._ps, "atomic_write_bytes", real)
    write(tally_path(env), "n1\n")  # the plugin writes its own lines into the new tally
    sys.modules.pop(STATE, None)  # the second pass is a new process
    storage.migrate(lay, [tally_item(storage, env)], Log())
    assert storage.sync_tally_tail(lay, tally_path(env), Log()) == 6
    assert read(tally_path(env)) == b"n1\nt1\nt2\n"
    assert storage.sync_tally_tail(lay, tally_path(env), Log()) == 0


def test_sync_tail_missing_offset_entry_reads_from_zero(storage, env, monkeypatch):
    write(legacy_tally(env), "t1\nt2\n")
    lay = layout_for(storage, env)
    write(offsets_path(env), json.dumps({"/some/other/file": 0}))  # offsets exist, no entry for ours
    fail_writes_to(storage, monkeypatch, tally_path(env))
    storage.migrate(lay, [tally_item(storage, env)], Log())
    monkeypatch.undo()
    write(tally_path(env), "n1\n")
    assert storage.sync_tally_tail(lay, tally_path(env), Log()) == 6
    assert read(tally_path(env)) == b"n1\nt1\nt2\n"


def test_sync_tail_rotated_legacy_reads_from_zero(storage, env):
    write(legacy_tally(env), "t1\nt2\n")  # 6 bytes, shorter than the recorded offset
    lay = layout_for(storage, env)
    write(tally_path(env), "n1\n")
    write(offsets_path(env), json.dumps({legacy_tally(env): 100}))
    logs = Log()
    assert storage.sync_tally_tail(lay, tally_path(env), logs) == 6
    assert read(tally_path(env)) == b"n1\nt1\nt2\n"
    assert json.loads(read(offsets_path(env))) == {legacy_tally(env): 6}
    assert any("rotated" in str(args) for args in logs.infos)


class _Counting:
    def __init__(self, fh, sink):
        self._fh = fh
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()
        return False

    def read(self, *args):
        data = self._fh.read(*args)
        self._sink.append(len(data))
        return data

    def seek(self, *args):
        return self._fh.seek(*args)


def test_sync_tail_reads_only_past_offset(storage, env, monkeypatch):
    write(legacy_tally(env), b"line\n" * 20000)  # 100000 bytes
    lay = layout_for(storage, env)
    write(tally_path(env), b"")
    write(offsets_path(env), json.dumps({legacy_tally(env): 99000}))
    sink = []
    real_open = open

    def counting_open(path, mode="r", *args, **kwargs):
        fh = real_open(path, mode, *args, **kwargs)
        if str(path) == legacy_tally(env) and "r" in mode and "b" in mode:
            return _Counting(fh, sink)
        return fh

    monkeypatch.setattr(storage._ps, "open", counting_open, raising=False)
    assert storage.sync_tally_tail(lay, tally_path(env), Log()) == 1000
    assert sum(sink) == 1000, "the tail must seek past the offset instead of reading the whole file"


def test_previous_dir_fallback_is_guarded(storage, env):
    write(P(env.plugins, "escape", "settings.json"), "ESCAPE")
    lay = storage.Layout(False, env.new, env.plugins, "../escape", "test")
    item = storage.Item("SETTINGS", P(env.old, "stream_mapparr_settings.json"),
                        P(env.new, "settings.json"), "file", rel="settings.json")
    storage.migrate(lay, [item], Log())
    assert not os.path.exists(P(env.new, "settings.json"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_atomic_write_creates_owner_only_file(storage, tmp_path):
    target = str(tmp_path / "state.json")
    old_umask = os.umask(0)
    try:
        storage._ps.atomic_write_bytes(target, b"x")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(os.stat(target).st_mode) & 0o077 == 0


def test_makedirs_mode_and_refuse_real_data_before_makedirs(storage, env, monkeypatch):
    calls = []
    real_makedirs = os.makedirs

    def recording(path, *args, **kwargs):
        calls.append((path, kwargs))
        return real_makedirs(path, *args, **kwargs)

    plugin_dir = P(env.plugins, "stream-mapparr")
    os.makedirs(plugin_dir)
    monkeypatch.setattr(storage._ps.os, "makedirs", recording)
    monkeypatch.setattr(storage._ps, "_from_module_context", lambda plugin_dir, failures: (env.new, "test"))
    layout = storage.resolve(plugin_dir, "stream-mapparr", Log())
    assert layout.legacy is False
    storage.atomic_write_bytes(P(env.new, "sub", "x.json"), b"1")
    assert calls, "resolve and atomic_write_bytes must create directories"
    for path, kwargs in calls:
        assert kwargs.get("mode") == 0o755, f"makedirs({path}) has no mode=0o755"

    monkeypatch.setattr(storage._ps, "REFUSE_REAL_DATA", True)
    monkeypatch.setattr(storage._ps, "_from_module_context",
                        lambda plugin_dir, failures: ("/data/plugins/_data/stream-mapparr", "test"))
    calls.clear()
    with pytest.raises(RuntimeError):
        storage.resolve(plugin_dir, "stream-mapparr", Log())
    assert calls == [], "REFUSE_REAL_DATA must be checked before any makedirs"


def test_tally_two_existing_sources_counted_once(storage, env):
    # Source (a), the last finished pass's data dir, beats the legacy file. Both exist.
    prev = P(env.plugins, "_data", "old-dir")
    write(P(prev, "match_counts.jsonl"), "t1\n")
    write(legacy_tally(env), "t1\nt2\n")
    write(P(env.plugins, "_data", "stream-mapparr.active"), json.dumps({"data_dir": prev}))
    lay = layout_for(storage, env)
    storage.migrate(lay, [tally_item(storage, env)], Log())
    assert read(tally_path(env)) == b"t1\n"
    assert storage.sync_tally_tail(lay, tally_path(env), Log()) == 0
    assert read(tally_path(env)) == b"t1\n", "the second existing source must not be counted on top of the copy"


def test_sync_tail_absent_offsets_file_same_process_reads_legacy(storage, env, monkeypatch):
    write(legacy_tally(env), "t1\nt2\n")
    lay = layout_for(storage, env)
    fail_writes_to(storage, monkeypatch, tally_path(env))
    storage.migrate(lay, [tally_item(storage, env)], Log())
    monkeypatch.undo()
    assert not os.path.exists(offsets_path(env)), "the failed copy must leave no offsets file"
    write(tally_path(env), "n1\n")
    assert storage.sync_tally_tail(lay, tally_path(env), Log()) == 6
    assert read(tally_path(env)) == b"n1\nt1\nt2\n"


def test_previous_dir_active_pointer_is_guarded(storage, env):
    write(P(env.plugins, "escape", "settings.json"), "ESCAPE")
    write(P(env.plugins, "_data", "stream-mapparr.active"), json.dumps({"data_dir": P(env.plugins, "escape")}))
    lay = layout_for(storage, env)
    item = storage.Item("SETTINGS", P(env.old, "stream_mapparr_settings.json"),
                        P(env.new, "settings.json"), "file", rel="settings.json")
    storage.migrate(lay, [item], Log())
    assert not os.path.exists(P(env.new, "settings.json"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_atomic_write_fixes_mode_of_stale_temp_file(storage, tmp_path):
    target = str(tmp_path / "state.json")
    stale = storage._ps._temp_path(target)
    write(stale, "left by a crash")
    os.chmod(stale, 0o666)
    old_umask = os.umask(0)
    try:
        storage._ps.atomic_write_bytes(target, b"x")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(os.stat(target).st_mode) & 0o077 == 0
