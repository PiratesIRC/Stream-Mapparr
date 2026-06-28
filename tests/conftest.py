"""Shared test fixtures for Stream-Mapparr.

The plugin runs *inside* Dispatcharr's Django backend, so plugin.py imports
Django, pytz, and the Dispatcharr ORM at module load. None of that is available
in a bare CI checkout. This conftest installs lightweight stubs for those
modules so the plugin can be imported and its pure helpers tested in isolation.

fuzzy_matcher.py has NO Django dependency (stdlib + optional rapidfuzz only), so
it is loaded directly from its file path with no stubbing required.
"""

import importlib.util
import sys
import types
from datetime import datetime, timezone as _stdlib_tz
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Repo layout: <root>/Stream-Mapparr/{plugin.py,fuzzy_matcher.py,*_channels.json}
PLUGIN_DIR = Path(__file__).resolve().parent.parent / "Stream-Mapparr"
_PKG = "stream_mapparr_under_test"

# The plugin vendors matching_core.py beside fuzzy_matcher.py; put the inner folder on
# sys.path so a standalone (non-package) load of fuzzy_matcher can import it absolutely
# (the fuzzy_matcher import tries a relative import first, then falls back to this).
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))


def _install_runtime_stubs():
    """Inject fake django / apps / core / pytz modules into sys.modules.

    Installed unconditionally (overwriting any real Django) so tests never
    depend on a configured Django settings module.
    """
    # --- pytz: prefer the real package, fall back to a minimal stub ---
    if "pytz" not in sys.modules:
        try:  # pragma: no cover - depends on environment
            import pytz  # noqa: F401
        except ImportError:
            pytz_stub = types.ModuleType("pytz")
            pytz_stub.utc = _stdlib_tz.utc
            pytz_stub.timezone = lambda name: _stdlib_tz.utc
            pytz_stub.all_timezones = []
            sys.modules["pytz"] = pytz_stub

    # --- django.utils.timezone / django.db.transaction ---
    django = types.ModuleType("django")
    django_utils = types.ModuleType("django.utils")
    django_tz = types.ModuleType("django.utils.timezone")
    django_tz.now = lambda: datetime.now(_stdlib_tz.utc)  # aware UTC, like Django USE_TZ
    django_utils.timezone = django_tz
    django_db = types.ModuleType("django.db")
    django_db.transaction = MagicMock(name="transaction")
    sys.modules.update({
        "django": django,
        "django.utils": django_utils,
        "django.utils.timezone": django_tz,
        "django.db": django_db,
    })

    # --- apps.channels.models (Dispatcharr ORM) ---
    apps = types.ModuleType("apps"); apps.__path__ = []
    apps_channels = types.ModuleType("apps.channels"); apps_channels.__path__ = []
    models = types.ModuleType("apps.channels.models")
    for name in ("Channel", "ChannelGroup", "ChannelProfile",
                 "ChannelProfileMembership", "ChannelStream", "Stream"):
        setattr(models, name, MagicMock(name=name))
    sys.modules.update({
        "apps": apps,
        "apps.channels": apps_channels,
        "apps.channels.models": models,
    })

    # --- core.utils.send_websocket_update ---
    core = types.ModuleType("core"); core.__path__ = []
    core_utils = types.ModuleType("core.utils")
    core_utils.send_websocket_update = MagicMock(name="send_websocket_update")
    sys.modules.update({"core": core, "core.utils": core_utils})


def _load_from_path(module_name, file_name):
    spec = importlib.util.spec_from_file_location(module_name, PLUGIN_DIR / file_name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_fuzzy_matcher():
    """Import fuzzy_matcher.py directly (no package context needed)."""
    name = f"{_PKG}_fuzzy_matcher"
    if name not in sys.modules:
        _load_from_path(name, "fuzzy_matcher.py")
    return sys.modules[name]


def load_plugin():
    """Import plugin.py as a package submodule so its relative imports resolve.

    plugin.py does `from .fuzzy_matcher import FuzzyMatcher`, which only works if
    it is loaded as a member of a package whose __path__ points at PLUGIN_DIR.
    """
    _install_runtime_stubs()
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(PLUGIN_DIR)]
        sys.modules[_PKG] = pkg
    if f"{_PKG}.fuzzy_matcher" not in sys.modules:
        _load_from_path(f"{_PKG}.fuzzy_matcher", "fuzzy_matcher.py")
    if f"{_PKG}.plugin" not in sys.modules:
        _load_from_path(f"{_PKG}.plugin", "plugin.py")
    return sys.modules[f"{_PKG}.plugin"]


@pytest.fixture(scope="session")
def fuzzy_module():
    return load_fuzzy_matcher()


@pytest.fixture(scope="session")
def plugin_module():
    return load_plugin()


@pytest.fixture
def matcher(fuzzy_module, tmp_path):
    """A FuzzyMatcher with NO channel databases loaded (fast, deterministic).

    Pointing plugin_dir at an empty tmp dir skips loading the multi-MB real
    *_channels.json files, which the unit tests don't need.
    """
    def _make(threshold=85):
        return fuzzy_module.FuzzyMatcher(plugin_dir=str(tmp_path), match_threshold=threshold)
    return _make


def channel_db_files():
    """All *_channels.json paths in the plugin dir (used to parametrize tests)."""
    return sorted(PLUGIN_DIR.glob("*_channels.json"))
