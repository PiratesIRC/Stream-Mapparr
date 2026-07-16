"""bug-136 — scheduler state must survive Dispatcharr plugin module RE-IMPORT.

bug-127 made scheduler arming idempotent, but its state lived in MODULE-LEVEL
globals, which only survive Plugin re-instantiation. Dispatcharr's connect-event
dispatch (apps/connect/utils.py) runs discover_plugins(use_cache=True) on every
client_connect/disconnect/channel_start/stop, and the loader's reload-token
protocol touches the token on every force reload — so with >=2 worker processes
one force reload (e.g. installing a plugin update) triggers a PERPETUAL
cross-process force-reload ping-pong. Each force reload purges the plugin's
modules from sys.modules and re-execs them, wiping the globals: the bug-127
fast path never matches, a NEW scheduler thread starts per event dispatch, and
the OLD thread is orphaned forever (its stop Event lives in the discarded
module namespace). ~6 leaked threads/min while zapping in the reporter's log,
degrading the gevent workers to nginx 504 = "the crash".

Fix under test: scheduler state (thread, stop Event, signature, lock) is parked
in a synthetic sys.modules entry whose name matches none of the loader's purge
patterns (package prefix, alias prefix, path-based via __file__), so a fresh
import finds the live thread and re-arming stays a true no-op.
"""

import importlib.util
import os
import sys
import threading
import types

import conftest
import pytest

_PKG = "smr_bug136_reload"
_STATE_NAME = "_stream_mapparr_scheduler_state"
_SCHED_THREAD_NAME = "stream-mapparr-scheduler"


# --------------------------------------------------------------------------- #
# Harness: load / purge the plugin the way Dispatcharr's loader does
# --------------------------------------------------------------------------- #

def _exec_plugin(pkg_name=_PKG):
    """Fresh exec of plugin.py under `pkg_name`, like the loader's _load_module_from_path.

    A force reload re-execs the SAME module name into a NEW module object with
    fresh globals — package identity is irrelevant to the bug, fresh globals are.
    """
    conftest._install_runtime_stubs()
    pkg = types.ModuleType(pkg_name)
    pkg.__path__ = [str(conftest.PLUGIN_DIR)]
    sys.modules[pkg_name] = pkg
    for sub in ("fuzzy_matcher", "plugin"):
        name = f"{pkg_name}.{sub}"
        spec = importlib.util.spec_from_file_location(name, conftest.PLUGIN_DIR / f"{sub}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{pkg_name}.plugin"]


def _purge(pkg_name=_PKG):
    """Mimic loader._unload_package: pop the package and every submodule."""
    for name in list(sys.modules):
        if name == pkg_name or name.startswith(pkg_name + "."):
            sys.modules.pop(name, None)


def _sched_threads():
    return {t for t in threading.enumerate()
            if t.name == _SCHED_THREAD_NAME and t.is_alive()}


def _stop_everything(*mods):
    """Best-effort cleanup of scheduler threads from BOTH generations of code.

    Pre-fix orphans listen to their own module's _stop_event global; post-fix
    threads listen to the Event parked in the state module.
    """
    for mod in mods:
        ev = getattr(mod, "_stop_event", None)
        if ev is not None:
            ev.set()
    state = sys.modules.get(_STATE_NAME)
    if state is not None:
        ev = getattr(state, "stop_event", None)
        if ev is not None:
            ev.set()
    for t in _sched_threads():
        t.join(timeout=6)


@pytest.fixture(autouse=True)
def _isolated_scheduler_state():
    """Each test starts and ends with no parked state and no live scheduler."""
    sys.modules.pop(_STATE_NAME, None)
    yield
    _stop_everything()
    sys.modules.pop(_STATE_NAME, None)
    _purge()


# --------------------------------------------------------------------------- #
# The leak repro (the reporter's log, distilled)
# --------------------------------------------------------------------------- #

def test_reimport_with_unchanged_schedule_reuses_live_thread():
    """A force reload + re-arm with the SAME schedule must be a no-op: the
    original thread stays THE thread, and no second thread appears. This is
    the ~6-leaks/min line from the reporter's log."""
    baseline = _sched_threads()
    mod1 = _exec_plugin()
    mod2 = None
    try:
        p1 = mod1.Plugin()
        p1._start_background_scheduler({"scheduled_times": "0330"})
        started = _sched_threads() - baseline
        assert len(started) == 1, "first arm must start exactly one scheduler thread"
        t1 = next(iter(started))

        _purge()
        mod2 = _exec_plugin()  # fresh module object, fresh globals
        p2 = mod2.Plugin()
        p2._start_background_scheduler({"scheduled_times": "0330"})

        after = _sched_threads() - baseline
        assert after == {t1}, (
            "re-arm after a module re-import must reuse the live scheduler thread; "
            f"got {len(after)} thread(s) — a new thread means the old one leaked"
        )
    finally:
        _stop_everything(mod1, *( [mod2] if mod2 else [] ))


def test_stop_after_reimport_stops_the_original_thread():
    """The orphan half of the bug: after a re-import, stopping the scheduler
    must actually stop the thread started by the PREVIOUS module generation
    (pre-fix, the fresh globals made stop a silent no-op — unstoppable orphan)."""
    baseline = _sched_threads()
    mod1 = _exec_plugin()
    mod2 = None
    try:
        p1 = mod1.Plugin()
        p1._start_background_scheduler({"scheduled_times": "0330"})
        t1 = next(iter(_sched_threads() - baseline))

        _purge()
        mod2 = _exec_plugin()
        p2 = mod2.Plugin()
        p2._stop_background_scheduler()
        t1.join(timeout=6)
        assert not t1.is_alive(), (
            "stop after a module re-import must stop the original thread — "
            "an alive thread here is the unstoppable orphan of bug-136"
        )
    finally:
        _stop_everything(mod1, *( [mod2] if mod2 else [] ))


def test_schedule_change_after_reimport_supersedes_old_thread():
    """A real schedule edit that lands after a re-import must stop the old
    thread and run exactly one new one (no orphan + relaunch)."""
    baseline = _sched_threads()
    mod1 = _exec_plugin()
    mod2 = None
    try:
        p1 = mod1.Plugin()
        p1._start_background_scheduler({"scheduled_times": "0330"})
        t1 = next(iter(_sched_threads() - baseline))

        _purge()
        mod2 = _exec_plugin()
        p2 = mod2.Plugin()
        p2._start_background_scheduler({"scheduled_times": "0445"})

        t1.join(timeout=6)
        assert not t1.is_alive(), "old-generation thread must be stopped on schedule change"
        after = _sched_threads() - baseline
        assert len(after) == 1, f"exactly one scheduler thread must survive, got {len(after)}"
    finally:
        _stop_everything(mod1, *( [mod2] if mod2 else [] ))


def test_version_bump_across_reimport_supersedes_old_thread():
    """A re-import that carries NEW plugin code (version bump = Dispatcharr
    hot-reload of a plugin update) must supersede the old-code thread even
    though the schedule is unchanged — the arm signature includes the version."""
    baseline = _sched_threads()
    mod1 = _exec_plugin()
    mod2 = None
    try:
        p1 = mod1.Plugin()
        p1._start_background_scheduler({"scheduled_times": "0330"})
        t1 = next(iter(_sched_threads() - baseline))

        _purge()
        mod2 = _exec_plugin()
        mod2.PluginConfig.PLUGIN_VERSION = "9.99.9999999"  # the "new code" marker
        p2 = mod2.Plugin()
        p2._start_background_scheduler({"scheduled_times": "0330"})

        t1.join(timeout=6)
        assert not t1.is_alive(), (
            "a version change must restart the scheduler so the new code runs"
        )
        after = _sched_threads() - baseline
        assert len(after) == 1
    finally:
        _stop_everything(mod1, *( [mod2] if mod2 else [] ))


# --------------------------------------------------------------------------- #
# The parked state must dodge every purge pattern in Dispatcharr's loader
# --------------------------------------------------------------------------- #

def test_scheduler_state_survives_loader_purge_predicates():
    """Replicates the three sys.modules purge predicates from Dispatcharr's
    apps/plugins/loader.py (_unload_package / _unload_alias /
    _unload_path_modules) and asserts the parked state module matches NONE,
    while the plugin module itself matches (sanity that the predicates work)."""
    mod1 = _exec_plugin()
    try:
        p1 = mod1.Plugin()
        p1._start_background_scheduler({"scheduled_times": "0330"})
        state = sys.modules.get(_STATE_NAME)
        assert state is not None, "arming must park scheduler state in sys.modules"

        def matches_prefix(name, prefix):
            return name == prefix or name.startswith(prefix + ".")

        # _unload_package / _unload_alias: name-prefix based. The alias for the
        # 'stream-mapparr' folder normalizes to 'stream_mapparr' — the state
        # name must not sit under it (i.e. must not be 'stream_mapparr' or
        # 'stream_mapparr.<x>').
        for prefix in (_PKG, "stream_mapparr", "plugins.stream_mapparr"):
            assert not matches_prefix(_STATE_NAME, prefix), (
                f"state module name {_STATE_NAME!r} would be purged by prefix {prefix!r}"
            )
        assert matches_prefix(f"{_PKG}.plugin", _PKG)  # sanity

        # _unload_path_modules: purges modules whose __file__ lives under the
        # plugin directory. The state module must have NO __file__.
        root = os.path.abspath(str(conftest.PLUGIN_DIR))
        state_file = getattr(state, "__file__", None)
        assert not (state_file and os.path.abspath(state_file).startswith(root)), (
            "state module must not carry a __file__ under the plugin dir"
        )
        plugin_file = getattr(mod1, "__file__", None)
        assert plugin_file and os.path.abspath(plugin_file).startswith(root)  # sanity
    finally:
        _stop_everything(mod1)


# --------------------------------------------------------------------------- #
# QA-review findings (M1/M2/M3) — hot-path convergence and supersession
# --------------------------------------------------------------------------- #

def test_invalid_times_settles_into_fast_path_noop(monkeypatch):
    """M1: an UNPARSEABLE schedule ("banana", or the "0330 0445" space-typo) must
    not put every request back on the locked path. The locked path starts no
    thread for invalid times; the fast path must recognize that outcome as the
    settled state instead of retrying (lock + re-parse + INFO log) per request."""
    mod1 = _exec_plugin()
    try:
        p1 = mod1.Plugin()
        calls = []
        orig = mod1.Plugin._start_background_scheduler_locked

        def counting(self, settings, state):
            calls.append(1)
            return orig(self, settings, state)

        monkeypatch.setattr(mod1.Plugin, "_start_background_scheduler_locked", counting)
        for _ in range(3):
            p1._start_background_scheduler({"scheduled_times": "banana"})
        assert not _sched_threads(), "invalid times must never start a thread"
        assert len(calls) == 1, (
            "re-arm with an unchanged INVALID schedule must be a fast-path no-op; "
            f"locked path ran {len(calls)} times — that's a per-request lock trip (bug-127 redux)"
        )
    finally:
        _stop_everything(mod1)


def test_settings_content_change_restarts_scheduler():
    """M2: the scheduler thread closes over its arm-time settings and fires the
    scheduled actions with them, so a settings change that does NOT touch
    scheduled_times (e.g. profile_name — which scopes what Match & Assign
    REPLACES) must still supersede the live thread."""
    baseline = _sched_threads()
    mod1 = _exec_plugin()
    try:
        p1 = mod1.Plugin()
        p1._start_background_scheduler({"scheduled_times": "0330", "profile_name": "A"})
        t1 = next(iter(_sched_threads() - baseline))

        p1._start_background_scheduler({"scheduled_times": "0330", "profile_name": "B"})
        after = _sched_threads() - baseline
        assert len(after) == 1, f"exactly one scheduler thread must survive, got {len(after)}"
        t2 = next(iter(after))
        assert t2 is not t1, (
            "a settings-content change must restart the scheduler so the run fires "
            "with the new settings, not the arm-time snapshot"
        )
        # ...and an arm with the SAME full settings stays a no-op
        p1._start_background_scheduler({"scheduled_times": "0330", "profile_name": "B"})
        assert next(iter(_sched_threads() - baseline)) is t2
    finally:
        _stop_everything(mod1)


def test_timed_out_stop_releases_thread_and_arm_state(monkeypatch):
    """M3: a thread that outlives the join timeout (an in-flight scheduled run
    ignores the stop event until its next loop tick) must not stay parked as
    state.bg_thread — every later request would pay a lock-held join. The stop
    must release the reference and clear the arm signature (so a later re-enable
    genuinely re-arms, and the fast path converges to a no-op)."""
    mod1 = _exec_plugin()
    wedged = None
    release = threading.Event()
    try:
        p1 = mod1.Plugin()
        state = sys.modules.get(_STATE_NAME) or mod1._scheduler_state()
        wedged = threading.Thread(target=release.wait, args=(6,),
                                  name=_SCHED_THREAD_NAME, daemon=True)
        wedged.start()
        state.bg_thread = wedged
        state.stop_event = threading.Event()
        state.signature = ("v", "0330")
        monkeypatch.setattr(mod1.PluginConfig, "SCHEDULER_STOP_TIMEOUT", 0.05)

        p1._stop_background_scheduler()

        assert state.bg_thread is None, (
            "timed-out join must release the thread reference so the fast path converges"
        )
        assert state.signature is None, (
            "an explicit stop must clear the arm signature — a later arm with the "
            "same settings (plugin re-enable) must actually restart the scheduler"
        )
    finally:
        release.set()
        if wedged is not None:
            wedged.join(timeout=6)
        _stop_everything(mod1)


def test_dead_thread_self_heals_on_rearm():
    """Characterization lock (passes before and after the M1-M3 changes): if the
    live thread dies unexpectedly, a re-arm with unchanged settings must notice
    and restart it rather than fast-path over the corpse."""
    baseline = _sched_threads()
    mod1 = _exec_plugin()
    try:
        p1 = mod1.Plugin()
        settings = {"scheduled_times": "0330"}
        p1._start_background_scheduler(settings)
        t1 = next(iter(_sched_threads() - baseline))
        # kill it out-of-band
        state = sys.modules[_STATE_NAME]
        state.stop_event.set()
        t1.join(timeout=6)
        assert not t1.is_alive()

        p1._start_background_scheduler(settings)
        after = _sched_threads() - baseline
        assert len(after) == 1 and next(iter(after)) is not t1, (
            "re-arm over a dead thread must self-heal (restart)"
        )
    finally:
        _stop_everything(mod1)
