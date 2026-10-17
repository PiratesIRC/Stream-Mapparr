"""The placeholder scan really hands the worker back, at runtime.

tests/test_worker_yield.py proves from the source that every hot loop CALLS
_cooperative_yield(). It cannot prove that the call reaches the gevent hub, or
how much work runs between two calls, because outside Dispatcharr time.sleep
is not gevent.sleep and the yield does nothing. These tests point time.sleep at
gevent.sleep for their own duration, which is what Dispatcharr's early
monkey-patching does for the whole process, and run the scan inside the
hub_hold guard (tests/conftest.py).

The scan is the one hot path that runs without the ORM: the plugin hands
placeholder_scan.scan_families its yield as a callback. Measured 2026-10-17 on
the development machine with 25,000 names and 50 patterns: 554 ms in total,
49 yields, the longest gap between two yields 25.7 ms. The 100 ms bound has
about four times headroom. Without the yield the same scan holds the worker for
the whole run.
"""
import re

import pytest

from placeholder_scan import scan_families


def _streams(n=25000):
    return [{"id": i, "name": f"US: EVENT {i % 400:03d} | SPORT {i}", "tvg_id": ""}
            for i in range(n)]


def _patterns(n=50):
    return [re.compile(rf"^(?:US: )?NOTHING{k} .*\d+$", re.IGNORECASE) for k in range(n)]


@pytest.fixture
def gevent_sleep(plugin_module, monkeypatch):
    """time.sleep as gevent.sleep for this test only, as monkey-patching makes it."""
    import gevent
    monkeypatch.setattr(plugin_module.time, "sleep", gevent.sleep)


def _scan_through_the_plugin_yield(plugin_module, on_yield="plugin"):
    plugin = plugin_module.Plugin.__new__(plugin_module.Plugin)
    cfg = plugin_module.PluginConfig
    callback = (lambda _index: plugin._cooperative_yield()) if on_yield == "plugin" else on_yield
    return scan_families(_streams(), _patterns(), on_yield=callback,
                         yield_every=cfg.REGEX_YIELD_EVERY,
                         max_name_len=cfg.REGEX_NAME_MAX_LEN,
                         budget_seconds=cfg.REGEX_PASS_BUDGET_S)


def test_the_scan_never_holds_the_worker_long(plugin_module, gevent_sleep, hub_hold):
    with hub_hold(max_ms=100):
        families = _scan_through_the_plugin_yield(plugin_module)
    assert families, "the scan found no family, so it did not do the work being timed"


def test_the_same_scan_without_the_yield_is_caught(plugin_module, gevent_sleep, hub_hold):
    """Positive control: the identical scan with no yield holds the worker throughout."""
    with pytest.raises(AssertionError):
        with hub_hold(max_ms=100):
            _scan_through_the_plugin_yield(plugin_module, on_yield=None)


def test_the_yield_does_nothing_unless_time_sleep_is_gevent(plugin_module, hub_hold):
    """Without the patch the plugin's yield switches nothing, which is why the
    tests above patch time.sleep and why the AST tests alone cannot show this."""
    with pytest.raises(AssertionError):
        with hub_hold(max_ms=100):
            _scan_through_the_plugin_yield(plugin_module)
