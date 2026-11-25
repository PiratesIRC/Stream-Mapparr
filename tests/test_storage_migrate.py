"""storage.migrate copies the old /data state once; sync_tally_tail carries the tally tail.

Every path is a temp directory standing in for /data and <plugins>/_data. The process
flag lives in sys.modules, so an autouse fixture clears it around each test.
"""
import glob
import os
import shutil
import sys
import threading
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


def layout_for(storage, env, data_dir=None, key="stream-mapparr"):
    return storage.Layout(False, data_dir or env.new, env.plugins, key, "test")


def P(*parts):
    return os.path.join(*parts)


def settings_item(storage, env, data_dir=None):
    data_dir = data_dir or env.new
    return storage.Item("SETTINGS", P(env.old, "stream_mapparr_settings.json"),
                        P(data_dir, "settings.json"), "file", rel="settings.json")


def tally_item(storage, env, data_dir=None):
    data_dir = data_dir or env.new
    return storage.Item("TALLY", P(env.old, "stream_mapparr_match_counts.jsonl"),
                        P(data_dir, "match_counts.jsonl"), "tally", rel="match_counts.jsonl")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def test_copies_missing_file_byte_identical(storage, env):
    src = P(env.old, "stream_mapparr_settings.json")
    write(src, b'{"a": 1}\n')
    mtime = os.stat(src).st_mtime_ns
    res = storage.migrate(layout_for(storage, env), [settings_item(storage, env)], Log())
    assert read(P(env.new, "settings.json")) == b'{"a": 1}\n'
    assert read(src) == b'{"a": 1}\n'
    assert os.stat(src).st_mtime_ns == mtime
    assert res.failed == [] and res.skipped_pass is False
    assert glob.glob(P(env.new, ".*tmp*")) == []


def test_existing_new_file_wins(storage, env):
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    write(P(env.new, "settings.json"), "NEW")
    storage.migrate(layout_for(storage, env), [settings_item(storage, env)], Log())
    assert read(P(env.new, "settings.json")) == b"NEW"
    assert read(P(env.old, "stream_mapparr_settings.json")) == b"OLD"


def test_existing_empty_data_dir_still_receives_copy(storage, env):
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    os.makedirs(env.new)
    storage.migrate(layout_for(storage, env), [settings_item(storage, env)], Log())
    assert read(P(env.new, "settings.json")) == b"OLD"


def test_nocopy_items_are_not_copied(storage, env):
    names = ["stream_mapparr_operation.lock", "stream_mapparr_scheduler.lock",
             "stream_mapparr_m3u_refresh.lock", "stream_mapparr_m3u_refresh_pending"]
    items = []
    for name in names:
        write(P(env.old, name), "x")
        items.append(storage.Item(name, P(env.old, name), P(env.new, name), "nocopy"))
    storage.migrate(layout_for(storage, env), items, Log())
    for name in names:
        assert not os.path.exists(P(env.new, name))


def test_directory_copied_file_by_file(storage, env):
    write(P(env.old, "snapshots", "a.json"), "OLD-A")
    write(P(env.old, "snapshots", "b.json"), "OLD-B")
    write(P(env.new, "snapshots", "a.json"), "NEW-A")
    item = storage.Item("SNAP", P(env.old, "snapshots"), P(env.new, "snapshots"), "dir",
                        rel="snapshots")
    res = storage.migrate(layout_for(storage, env), [item], Log())
    assert read(P(env.new, "snapshots", "a.json")) == b"NEW-A"
    assert read(P(env.new, "snapshots", "b.json")) == b"OLD-B"
    assert res.copied == [P(env.new, "snapshots", "b.json")]


def test_source_a_active_dir_beats_legacy(storage, env):
    foo = P(env.plugins, "_data", "foo")
    write(P(foo, "settings.json"), "A")
    write(P(env.old, "stream_mapparr_settings.json"), "B")
    write(P(env.plugins, "_data", "stream-mapparr.active"), '{"data_dir": "%s"}' % foo.replace("\\", "\\\\"))
    storage.migrate(layout_for(storage, env), [settings_item(storage, env)], Log())
    assert read(P(env.new, "settings.json")) == b"A"


def test_source_a_folder_key_dir_when_no_active(storage, env):
    write(P(env.plugins, "_data", "stream-mapparr", "settings.json"), "A")
    write(P(env.old, "stream_mapparr_settings.json"), "B")
    data = P(env.plugins, "_data", "hubslug")
    storage.migrate(layout_for(storage, env, data_dir=data), [settings_item(storage, env, data)], Log())
    assert read(P(data, "settings.json")) == b"A"


def test_marker_stops_legacy_reads(storage, env):
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    lay = layout_for(storage, env)
    storage.migrate(lay, [settings_item(storage, env)], Log())
    assert os.path.exists(P(env.plugins, "_data", "stream-mapparr.migrated"))
    shutil.rmtree(env.new)
    sys.modules.pop(STATE, None)
    storage.migrate(lay, [settings_item(storage, env)], Log())
    assert not os.path.exists(P(env.new, "settings.json"))
    assert read(P(env.old, "stream_mapparr_settings.json")) == b"OLD"


def test_failed_pass_writes_no_marker_or_active(storage, env, monkeypatch):
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    bad = P(env.old, "stream_mapparr_settings.json")
    real = storage._ps._read_bytes

    def denied(path):
        if path == bad:
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(storage._ps, "_read_bytes", denied)
    res = storage.migrate(layout_for(storage, env), [settings_item(storage, env)], Log())
    assert res.failed == ["settings.json"]
    assert not os.path.exists(P(env.plugins, "_data", "stream-mapparr.migrated"))
    assert not os.path.exists(P(env.plugins, "_data", "stream-mapparr.active"))


def test_unreadable_source_blocks_marker_only(storage, env, monkeypatch):
    bad = P(env.old, "stream_mapparr_settings.json")
    good = P(env.old, "stream_mapparr_processed.json")
    write(bad, "S")
    write(good, "P")
    real = storage._ps._read_bytes

    def denied(path):
        if path == bad:
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(storage._ps, "_read_bytes", denied)
    items = [settings_item(storage, env),
             storage.Item("PROCESSED", good, P(env.new, "processed.json"), "file", rel="processed.json")]
    lay = layout_for(storage, env)
    res = storage.migrate(lay, items, Log())
    assert read(P(env.new, "processed.json")) == b"P"
    assert not os.path.exists(P(env.new, "settings.json"))
    assert res.failed == ["settings.json"]
    assert not os.path.exists(P(env.plugins, "_data", "stream-mapparr.migrated"))
    # Retry on the next start, with the source readable again.
    monkeypatch.setattr(storage._ps, "_read_bytes", real)
    sys.modules.pop(STATE, None)
    res2 = storage.migrate(lay, items, Log())
    assert res2.failed == []
    assert read(P(env.new, "settings.json")) == b"S"
    assert os.path.exists(P(env.plugins, "_data", "stream-mapparr.migrated"))


def test_second_slug_change_reaches_through_active(storage, env):
    foo = P(env.plugins, "_data", "foo")
    bar = P(env.plugins, "_data", "bar")
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    write(P(env.old, "stream_mapparr_match_counts.jsonl"), "t1\n")
    storage.migrate(layout_for(storage, env, data_dir=foo),
                    [settings_item(storage, env, foo), tally_item(storage, env, foo)], Log())
    # A worker under slug foo changes its own files after the first pass.
    write(P(foo, "settings.json"), "FOO")
    with open(P(foo, "match_counts.jsonl"), "ab") as fh:
        fh.write(b"t2\n")
    sys.modules.pop(STATE, None)
    storage.migrate(layout_for(storage, env, data_dir=bar),
                    [settings_item(storage, env, bar), tally_item(storage, env, bar)], Log())
    assert read(P(bar, "settings.json")) == b"FOO"
    assert read(P(bar, "match_counts.jsonl")) == b"t1\nt2\n"
    import json
    with open(P(bar, "match_counts.offsets.json")) as fh:
        offsets = json.load(fh)
    assert list(offsets) == [P(foo, "match_counts.jsonl")]
    assert offsets[P(foo, "match_counts.jsonl")] == 6


def test_tally_and_install_id_byte_identical(storage, env):
    write(P(env.old, "stream_mapparr_match_counts.jsonl"), b"a\nb\n")
    write(P(env.old, "usage", "stream-mapparr", "install_id"), b"abc-123")
    items = [tally_item(storage, env),
             storage.Item("USAGE", P(env.old, "usage"), P(env.new, "usage"), "usage_dir",
                          rel="usage")]
    res = storage.migrate(layout_for(storage, env), items, Log())
    assert read(P(env.new, "match_counts.jsonl")) == b"a\nb\n"
    assert read(P(env.new, "usage", "stream-mapparr", "install_id")) == b"abc-123"
    assert res.install_id_blocked is False


def test_beside_last_run_record_copied_once(storage, env):
    lay = layout_for(storage, env)
    write(P(env.old, "stream_mapparr_scheduler_last_run.json"), "L1")
    item = storage.Item("LAST_RUN", P(env.old, "stream_mapparr_scheduler_last_run.json"),
                        lay.beside(".scheduler_last_run.json"), "beside")
    storage.migrate(lay, [item], Log())
    assert read(lay.beside(".scheduler_last_run.json")) == b"L1"


def test_beside_existing_file_wins(storage, env):
    lay = layout_for(storage, env)
    write(P(env.old, "stream_mapparr_scheduler_last_run.json"), "L1")
    write(lay.beside(".scheduler_last_run.json"), "L2")
    item = storage.Item("LAST_RUN", P(env.old, "stream_mapparr_scheduler_last_run.json"),
                        lay.beside(".scheduler_last_run.json"), "beside")
    storage.migrate(lay, [item], Log())
    assert read(lay.beside(".scheduler_last_run.json")) == b"L2"


def test_install_id_copy_failure_blocks(storage, env, monkeypatch):
    install = P(env.old, "usage", "stream-mapparr", "install_id")
    write(install, b"abc-123")
    items = [storage.Item("USAGE", P(env.old, "usage"), P(env.new, "usage"), "usage_dir",
                          rel="usage")]
    lay = layout_for(storage, env)
    real = storage._ps._read_bytes

    def denied(path):
        if path == install:
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(storage._ps, "_read_bytes", denied)
    res = storage.migrate(lay, items, Log())
    assert res.install_id_blocked is True
    assert res.skipped_pass is False
    assert not os.path.exists(P(env.plugins, "_data", "stream-mapparr.migrated"))
    # The flag is not set, so the next call runs the pass again and succeeds.
    monkeypatch.setattr(storage._ps, "_read_bytes", real)
    res2 = storage.migrate(lay, items, Log())
    assert res2.skipped_pass is False
    assert res2.install_id_blocked is False
    assert read(P(env.new, "usage", "stream-mapparr", "install_id")) == b"abc-123"
    res3 = storage.migrate(lay, items, Log())
    assert res3.skipped_pass is True


def test_second_call_is_noop_without_lock(storage, env, monkeypatch):
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    lay = layout_for(storage, env)
    storage.migrate(lay, [settings_item(storage, env)], Log())

    def no_lock(path):
        raise AssertionError("the hot path must not lock")

    monkeypatch.setattr(storage._ps, "_lock", no_lock)
    res = storage.migrate(lay, [settings_item(storage, env)], Log())
    assert res.skipped_pass is True
    assert res.copied == [] and res.failed == []


def test_temp_name_outside_tally_glob(storage, env):
    write(P(env.old, "stream_mapparr_match_counts.jsonl"), "t1\n")
    tally = P(env.new, "match_counts.jsonl")
    storage.migrate(layout_for(storage, env), [tally_item(storage, env)], Log())
    assert glob.glob(tally + "*") == [tally]
    assert os.path.basename(storage._ps._temp_path(tally)).startswith(".")


def test_tally_offset_equals_bytes_copied_even_if_source_grows(storage, env, monkeypatch):
    old_tally = P(env.old, "stream_mapparr_match_counts.jsonl")
    write(old_tally, "t1\n")
    grow = [True]
    real = storage._ps._read_bytes

    def growing(path):
        data = real(path)
        if path == old_tally and grow[0]:
            with open(path, "ab") as fh:
                fh.write(b"t2\n")
        return data

    monkeypatch.setattr(storage._ps, "_read_bytes", growing)
    lay = layout_for(storage, env)
    storage.migrate(lay, [tally_item(storage, env)], Log())
    grow[0] = False
    assert read(P(env.new, "match_counts.jsonl")) == b"t1\n"
    assert storage.sync_tally_tail(lay, P(env.new, "match_counts.jsonl"), Log()) == 3
    assert read(P(env.new, "match_counts.jsonl")) == b"t1\nt2\n"
    assert storage.sync_tally_tail(lay, P(env.new, "match_counts.jsonl"), Log()) == 0


def test_partial_last_line_not_counted(storage, env):
    old_tally = P(env.old, "stream_mapparr_match_counts.jsonl")
    write(old_tally, "t1\nt2")
    lay = layout_for(storage, env)
    tally = P(env.new, "match_counts.jsonl")
    storage.migrate(lay, [tally_item(storage, env)], Log())
    assert read(tally) == b"t1\n"
    assert storage.sync_tally_tail(lay, tally, Log()) == 0
    with open(old_tally, "ab") as fh:
        fh.write(b"\n")
    assert storage.sync_tally_tail(lay, tally, Log()) == 3
    assert read(tally) == b"t1\nt2\n"
    assert storage.sync_tally_tail(lay, tally, Log()) == 0


def test_crash_between_offsets_and_replace(storage, env, monkeypatch):
    write(P(env.old, "stream_mapparr_match_counts.jsonl"), "t1\n")
    tally = P(env.new, "match_counts.jsonl")
    real_replace = os.replace

    def crash_on_tally(src, dst):
        if os.path.basename(dst) == "match_counts.jsonl":
            raise OSError("simulated crash")
        return real_replace(src, dst)

    monkeypatch.setattr(storage._ps.os, "replace", crash_on_tally)
    lay = layout_for(storage, env)
    res = storage.migrate(lay, [tally_item(storage, env)], Log())
    assert res.failed == ["match_counts.jsonl"]
    assert not os.path.exists(tally)
    monkeypatch.setattr(storage._ps.os, "replace", real_replace)
    sys.modules.pop(STATE, None)
    res2 = storage.migrate(lay, [tally_item(storage, env)], Log())
    assert res2.failed == []
    assert read(tally) == b"t1\n"
    import json
    with open(P(env.new, "match_counts.offsets.json")) as fh:
        assert json.load(fh) == {P(env.old, "stream_mapparr_match_counts.jsonl"): 3}


def test_crash_between_offset_write_and_append(storage, env, monkeypatch):
    old_tally = P(env.old, "stream_mapparr_match_counts.jsonl")
    write(old_tally, "t1\n")
    lay = layout_for(storage, env)
    tally = P(env.new, "match_counts.jsonl")
    storage.migrate(lay, [tally_item(storage, env)], Log())
    with open(old_tally, "ab") as fh:
        fh.write(b"t2\n")

    def crash(path, data):
        raise OSError("simulated crash")

    monkeypatch.setattr(storage._ps, "_append_bytes", crash)
    assert storage.sync_tally_tail(lay, tally, Log()) == 0
    monkeypatch.undo()
    # The offset moved past the lost batch, so it is never appended later (undercount, not double).
    assert storage.sync_tally_tail(lay, tally, Log()) == 0
    assert read(tally) == b"t1\n"


def test_no_offsets_file_means_no_tail(storage, env):
    old_tally = P(env.old, "stream_mapparr_match_counts.jsonl")
    write(old_tally, "t1\nt2\n")
    tally = P(env.new, "match_counts.jsonl")
    write(tally, "own\n")
    lay = layout_for(storage, env)
    assert storage.sync_tally_tail(lay, tally, Log()) == 0
    assert read(tally) == b"own\n"


def test_legacy_layout_is_a_no_op(storage, env):
    lay = storage.Layout(True, None, None, "stream-mapparr", "legacy")
    res = storage.migrate(lay, [settings_item(storage, env)], Log())
    assert res.skipped_pass is True
    assert storage.sync_tally_tail(lay, P(env.old, "match_counts.jsonl"), Log()) == 0


def test_refuse_real_data_flag(storage, env, monkeypatch):
    monkeypatch.setattr(storage._ps, "REFUSE_REAL_DATA", True)
    item = storage.Item("SETTINGS", "/data/stream_mapparr_settings.json",
                        P(env.new, "settings.json"), "file", rel="settings.json")
    with pytest.raises(RuntimeError):
        storage.migrate(layout_for(storage, env), [item], Log())
    with pytest.raises(RuntimeError):
        storage.sync_tally_tail(layout_for(storage, env), "/data/match_counts.jsonl", Log())


def test_concurrent_migrate_calls(storage, env):
    pytest.importorskip("fcntl")
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    lay = layout_for(storage, env)
    results = []
    errors = []

    def run():
        try:
            results.append(storage.migrate(lay, [settings_item(storage, env)], Log()))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sum(len(r.copied) for r in results) == 1
    assert read(P(env.new, "settings.json")) == b"OLD"
    assert glob.glob(P(env.new, ".*tmp*")) == []


def test_corrupt_active_pointer_self_heals(storage, env):
    write(P(env.old, "stream_mapparr_settings.json"), "OLD")
    write(P(env.plugins, "_data", "stream-mapparr.active"), "not json {")
    lay = layout_for(storage, env)
    log = Log()
    res = storage.migrate(lay, [settings_item(storage, env)], log)
    assert res.failed == []
    assert read(P(env.new, "settings.json")) == b"OLD"
    assert os.path.exists(P(env.plugins, "_data", "stream-mapparr.migrated"))
    import json
    with open(P(env.plugins, "_data", "stream-mapparr.active")) as fh:
        assert json.load(fh) == {"data_dir": env.new}
    assert len(log.errors) == 1


def test_tail_during_copy_is_never_lost(storage, env, monkeypatch):
    old_tally = P(env.old, "stream_mapparr_match_counts.jsonl")
    write(old_tally, "t1\n")
    lay = layout_for(storage, env)
    tally = P(env.new, "match_counts.jsonl")
    real_write = storage.atomic_write_bytes
    tails = []
    threads = []

    def racing_write(path, data):
        if path == tally:
            # A concurrent tail starts now. On fcntl platforms it waits on match_counts.lock,
            # which the copy holds; on Windows it runs at once and finds no tally.
            with open(old_tally, "ab") as fh:
                fh.write(b"t2\n")
            t = threading.Thread(target=lambda: tails.append(
                storage.sync_tally_tail(lay, tally, Log())))
            threads.append(t)
            t.start()
            t.join(0.5)
        return real_write(path, data)

    monkeypatch.setattr(storage._ps, "atomic_write_bytes", racing_write)
    storage.migrate(lay, [tally_item(storage, env)], Log())
    for t in threads:
        t.join(5)
    monkeypatch.setattr(storage._ps, "atomic_write_bytes", real_write)
    storage.sync_tally_tail(lay, tally, Log())
    assert read(tally) == b"t1\nt2\n"
    assert read(tally).count(b"t2\n") == 1
