"""Three faults reported on 2026-11-22 after adding an M3U provider.

1. View Last Results printed the completion time as UTC ISO text, so a run that
   ended minutes earlier read as a time five hours away and looked like an old
   run.
2. The "started in background" message showed the estimate that gates
   synchronous versus background dispatch, which is priced pessimistically: it
   said 47 minutes for a run that took 5.6 seconds. The measured rate on that
   box (0.0009) was also below the old floor (0.001) and was thrown away.
3. Dispatcharr 0.34 builds the settings form only when the plugin loads, so a
   new M3U source stayed out of the M3U Sources list until a manual reload.
"""
import json
from datetime import datetime

import pytz


def _inst(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, msg):
        self.lines.append(msg)

    warning = debug = error = info


# --------------------------------------------------------------------------- #
# 1. Completion time in Dispatcharr's timezone, with its age
# --------------------------------------------------------------------------- #

def test_completed_at_is_shown_in_dispatcharr_time_with_age(plugin_module, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(p, "_dispatcharr_timezone", lambda: "America/Chicago")
    now = datetime(2026, 11, 22, 17, 55, 55, tzinfo=pytz.utc)
    label = p._completed_at_label("2026-11-22T17:52:50Z", now=now)
    assert label == "2026-11-22 11:52:50 CST (3m 5s ago)"


def test_completed_at_label_falls_back_to_the_stored_text(plugin_module):
    p = _inst(plugin_module)
    assert p._completed_at_label("not a time") == "not a time"


def test_last_results_toast_uses_the_local_label(plugin_module, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(p, "_completed_at_label", lambda v, now=None: "LOCAL-LABEL")
    text = p._format_last_results({"action": "add_streams_to_channels",
                                   "completed_at": "2026-11-22T17:52:50Z",
                                   "message": "Dry run complete."})
    assert "LOCAL-LABEL" in text
    assert "17:52:50Z" not in text


def test_check_progress_names_when_the_last_run_finished(plugin_module, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(p, "_load_progress_state", lambda: {"status": "complete"})
    monkeypatch.setattr(p, "_read_json_file", lambda path: {
        "action": "add_streams_to_channels", "message": "Dry run complete.",
        "completed_at": "2026-11-22T17:52:50Z"})
    monkeypatch.setattr(p, "_completed_at_label", lambda v, now=None: "LOCAL-LABEL")
    msg = p.view_check_progress_action({}, _Log())["message"]
    assert "finished LOCAL-LABEL" in msg


# --------------------------------------------------------------------------- #
# 2. The shown estimate uses the measured rate; dispatch stays pessimistic
# --------------------------------------------------------------------------- #

def _timing(tmp_path, rate):
    path = tmp_path / "timing.json"
    path.write_text(json.dumps({"seconds_per_group_per_1k_streams": rate}), encoding="utf-8")
    return str(path)


def test_the_rate_measured_on_2026_11_22_is_accepted(plugin_module, tmp_path):
    p = _inst(plugin_module)
    assert p._observed_rate(_timing(tmp_path, 0.0009)) == 0.0009


def test_shown_estimate_is_rescaled_to_the_measured_rate(plugin_module, tmp_path, monkeypatch):
    cfg = plugin_module.PluginConfig
    monkeypatch.setattr(cfg, "RUN_TIMING_FILE", _timing(tmp_path, 0.0009))
    p = _inst(plugin_module)
    # What the dispatcher computed for 100 groups over 33,743 streams.
    dispatch_eta = plugin_module.estimate_run_seconds(
        100, 33743, rate=p._dispatch_rate(0.0009))
    assert dispatch_eta > 3000                     # the misleading figure
    shown = p._display_eta_seconds(dispatch_eta)
    assert 2 <= shown <= 4, shown                  # the run measured 2.9 s


def test_shown_estimate_without_a_measurement_is_the_fallback(plugin_module, tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_module.PluginConfig, "RUN_TIMING_FILE", str(tmp_path / "absent.json"))
    p = _inst(plugin_module)
    assert p._display_eta_seconds(1234.0) == 1234.0
    assert p._display_eta_seconds(None) is None


def test_timing_is_recorded_precisely_enough_for_a_fast_box(plugin_module, tmp_path):
    p = _inst(plugin_module)
    path = str(tmp_path / "t.json")
    p._record_run_timing(path, 100, 33743, 2.9)
    rate = json.loads(open(path, encoding="utf-8").read())["seconds_per_group_per_1k_streams"]
    assert abs(rate - 2.9 / (100 * 33.743)) < 1e-6


# --------------------------------------------------------------------------- #
# 3. Reload requested once when the pick-list names change
# --------------------------------------------------------------------------- #

def _names(m3u):
    return {"channel_group_list": ["UK"], "stream_group_list": ["S1"], "m3u_source_list": m3u}


def test_first_call_records_without_reloading(plugin_module, tmp_path, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(type(p), "_pick_list_names", staticmethod(lambda: _names(["A"])))
    path = str(tmp_path / "names.json")
    assert p._pick_list_names_changed(_Log(), path=path) is False
    assert json.loads(open(path, encoding="utf-8").read())["m3u_source_list"] == ["A"]


def test_a_new_m3u_source_is_reported_once(plugin_module, tmp_path, monkeypatch):
    p = _inst(plugin_module)
    path = str(tmp_path / "names.json")
    current = {"v": _names(["A"])}
    monkeypatch.setattr(type(p), "_pick_list_names", staticmethod(lambda: current["v"]))
    p._pick_list_names_changed(_Log(), path=path)
    current["v"] = _names(["A", "New Provider"])
    assert p._pick_list_names_changed(_Log(), path=path) is True
    assert p._pick_list_names_changed(_Log(), path=path) is False


def test_an_empty_list_is_not_read_as_a_change(plugin_module, tmp_path, monkeypatch):
    """_pick_list_names returns an empty list on a database error."""
    p = _inst(plugin_module)
    path = str(tmp_path / "names.json")
    current = {"v": _names(["A"])}
    monkeypatch.setattr(type(p), "_pick_list_names", staticmethod(lambda: current["v"]))
    p._pick_list_names_changed(_Log(), path=path)
    current["v"] = _names([])
    assert p._pick_list_names_changed(_Log(), path=path) is False
    assert json.loads(open(path, encoding="utf-8").read())["m3u_source_list"] == ["A"]


def test_reload_request_added_on_a_034_host_when_names_changed(plugin_module, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(type(p), "_host_draws_034_form", staticmethod(lambda: True))
    monkeypatch.setattr(p, "_pick_list_names_changed", lambda logger: True)
    out = p._with_pick_list_reload(None, _Log())
    assert out["_dispatcharr"] == {"refresh": "plugin"}
    assert out["status"] == "success"
    kept = p._with_pick_list_reload({"status": "success", "message": "m"}, _Log())
    assert kept == {"status": "success", "message": "m", "_dispatcharr": {"refresh": "plugin"}}


def test_no_reload_request_without_a_change_or_on_an_old_host(plugin_module, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(type(p), "_host_draws_034_form", staticmethod(lambda: True))
    monkeypatch.setattr(p, "_pick_list_names_changed", lambda logger: False)
    assert p._with_pick_list_reload(None, _Log()) is None
    monkeypatch.setattr(type(p), "_host_draws_034_form", staticmethod(lambda: False))
    monkeypatch.setattr(p, "_pick_list_names_changed", lambda logger: True)
    assert p._with_pick_list_reload(None, _Log()) is None


def test_an_error_result_does_not_consume_the_change(plugin_module, monkeypatch):
    """The task raises on an error result before reading the reload request,
    so the change must stay unrecorded for the next refresh."""
    p = _inst(plugin_module)
    monkeypatch.setattr(type(p), "_host_draws_034_form", staticmethod(lambda: True))
    calls = []
    monkeypatch.setattr(p, "_pick_list_names_changed", lambda logger: calls.append(1) or True)
    err = {"status": "error", "message": "boom"}
    assert p._with_pick_list_reload(err, _Log()) is err
    assert calls == []


def test_run_routes_the_m3u_refresh_result_through_the_reload_check(plugin_module, monkeypatch):
    p = _inst(plugin_module)
    monkeypatch.setattr(p, "on_m3u_refresh_action", lambda s, l, c: None)
    monkeypatch.setattr(p, "_with_pick_list_reload", lambda result, logger: {"routed": True})
    assert p.run("on_m3u_refresh", {"event": "m3u_refresh"}, {}) == {"routed": True}
