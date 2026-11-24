"""storage._lock creates its lock file without any permission bit for other users.

The Dispatcharr Plugin Hub runs CodeQL on the released archive and refuses a pull
request on a high finding. Mode 0o644 on the lock file's os.open raised
py/overly-permissive-file on Dispatcharr/Plugins#628, so the mode is 0o660.
"""
import os
import re
import stat
import sys

import pytest

from conftest import _PKG, _load_from_path


@pytest.fixture
def storage():
    name = f"{_PKG}.storage"
    if name not in sys.modules:
        _load_from_path(name, "storage.py")
    return sys.modules[name]


def test_no_world_bits_in_any_os_open_mode():
    path = os.path.join(os.path.dirname(__file__), os.pardir, "Stream-Mapparr", "storage.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    modes = re.findall(r"os\.open\([^)]*?(0o[0-7]{3})\)", source)
    assert modes, "no os.open mode found; the check would pass vacuously"
    for mode in modes:
        assert int(mode, 8) & 0o007 == 0, f"os.open mode {mode} grants other users access"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_created_lock_file_has_no_world_bits(storage, tmp_path):
    lock_path = str(tmp_path / "x.lock")
    old_umask = os.umask(0)
    try:
        with storage._lock(lock_path):
            pass
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE(os.stat(lock_path).st_mode) & 0o007 == 0
