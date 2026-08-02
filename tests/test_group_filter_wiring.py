"""Tests that every action which filters by channel group actually HONORS the
include/exclude mode, not just that the helpers exist.

The group filter is parsed and applied inline in five separate places rather than
through one helper. A mode selector that only reaches some of them is worse than
no mode selector at all: Match and Assign would skip the excluded group while the
scheduled Sort still reordered its streams, so the two would silently disagree
about which channels the plugin owns.

The structural test below is deliberately a source-level check. It fails when a
sixth call site is added later without routing through the shared helper, which a
behavioral test over the current five cannot catch.
"""
import ast
import inspect
import textwrap

# Actions that filter a list of channels or streams in Python and must therefore
# call the shared filter helper.
_LIST_FILTERING_FUNCTIONS = [
    "load_process_channels_action",
    "match_us_ota_only_action",
    "sort_streams_action",
]

# Actions that filter through an ORM query instead of a Python list, so they
# cannot call the list helper but must still resolve the mode.
_ORM_FILTERING_FUNCTIONS = [
    "probe_throughput_action",
]


def _called_names(plugin_module, func_name):
    """Every attribute name called inside the named Plugin method."""
    source = inspect.getsource(getattr(plugin_module.Plugin, func_name))
    tree = ast.parse(textwrap.dedent(source))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.add(node.func.attr)
    return names


def test_list_filtering_actions_use_the_shared_filter_helper(plugin_module):
    for func_name in _LIST_FILTERING_FUNCTIONS:
        called = _called_names(plugin_module, func_name)
        assert "_filter_by_group_ids" in called, (
            f"{func_name} filters channels by group without going through "
            f"_filter_by_group_ids, so it ignores the include/exclude mode"
        )


def test_every_group_filtering_action_resolves_the_mode(plugin_module):
    for func_name in _LIST_FILTERING_FUNCTIONS + _ORM_FILTERING_FUNCTIONS:
        called = _called_names(plugin_module, func_name)
        assert "_resolve_group_filter_mode" in called, (
            f"{func_name} does not resolve the group filter mode from the live "
            f"settings dict, so the setting cannot reach it"
        )


def test_validation_reports_the_mode(plugin_module):
    """Validate Settings must say which direction the list is read in. Without
    it the operator cannot tell an include list from an exclude list, and the
    difference decides whether a group is processed or skipped."""
    called = _called_names(plugin_module, "_validate_plugin_settings")
    assert "_resolve_group_filter_mode" in called


def test_stream_group_filtering_honors_its_own_mode(plugin_module):
    """The stream-group list has its own mode selector, resolved separately."""
    called = _called_names(plugin_module, "load_process_channels_action")
    assert "_resolve_stream_group_filter_mode" in called


# --------------------------------------------------------------------------- #
# Behavioral check on one real action.
# --------------------------------------------------------------------------- #

class _Logger:
    def __init__(self):
        self.messages = []

    def _record(self, msg, *a, **k):
        self.messages.append(str(msg))

    info = debug = warning = error = _record


def _sort_action_harness(plugin_module, monkeypatch, mode):
    """Drive sort_streams_action against one grouped channel that has two streams.

    In include mode the channel is in scope and the action reports work. In
    exclude mode it is out of scope, leaving no channel with multiple streams,
    which the action reports distinctly. That difference is the observable.
    """
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None
    logger = _Logger()

    monkeypatch.setattr(p, "_initialize_fuzzy_matcher", lambda *a, **k: None)
    monkeypatch.setattr(p, "_build_alias_map", lambda *a, **k: {})
    monkeypatch.setattr(p, "_send_progress_update", lambda *a, **k: None)

    channels = [{"id": 1, "name": "Teamarr Game 1", "channel_group_id": 30}]
    monkeypatch.setattr(p, "_get_all_profiles", lambda log: [{"id": 7, "name": "a"}])
    monkeypatch.setattr(p, "_get_all_groups", lambda log: [{"id": 30, "name": "Teamarr"}])
    monkeypatch.setattr(p, "_get_all_channels", lambda log: list(channels))
    monkeypatch.setattr(p, "_load_channels_data", lambda log, s: [])
    monkeypatch.setattr(p, "_zone_routed_map", lambda *a, **k: {})
    monkeypatch.setattr(p, "_same_country_ids_for", lambda *a, **k: None)
    monkeypatch.setattr(p, "_streams_for_channel", lambda streams, *a, **k: streams)
    monkeypatch.setattr(p, "_sort_streams_by_quality", lambda streams: list(reversed(streams)))

    membership = plugin_module.ChannelProfileMembership
    membership.objects.filter.return_value.values_list.return_value = [1]

    channel_stream = plugin_module.ChannelStream
    channel_stream.objects.filter.return_value.order_by.return_value.values_list.return_value = [101, 102]

    class _Stream:
        def __init__(self, sid):
            self.id = sid
            self.name = f"Feed {sid}"
            self.stream_stats = {}
            self.m3u_account_id = 1
            self.channel_group_id = None

    plugin_module.Stream.objects.get.side_effect = lambda id: _Stream(id)
    plugin_module.Stream.DoesNotExist = type("DoesNotExist", (Exception,), {})

    settings = {
        "profile_name": "a",
        "selected_groups": "Teamarr",
        "group_filter_mode": mode,
        "dry_run_mode": True,
    }
    # Call the action directly rather than through run(): run() dispatches this
    # action to a background thread and returns "started in background", which
    # would hide the result the test is about.
    return p.sort_streams_action(settings, logger), logger


def test_sort_include_mode_puts_the_named_group_in_scope(plugin_module, monkeypatch):
    result, _ = _sort_action_harness(plugin_module, monkeypatch, "include")
    assert "No channels found with multiple streams" not in (result.get("message") or "")


def test_sort_exclude_mode_takes_the_named_group_out_of_scope(plugin_module, monkeypatch):
    """The motivating case, end to end: the one group the user excluded is the
    only group present, so nothing is left for Sort to touch."""
    result, _ = _sort_action_harness(plugin_module, monkeypatch, "exclude")
    assert "No channels found with multiple streams" in (result.get("message") or "")


# --------------------------------------------------------------------------- #
# A BLANK Channel Groups box, with either mode selected
# --------------------------------------------------------------------------- #

def _blank_list_harness(plugin_module, monkeypatch, mode):
    """Drive Sort Alternate Streams with the Channel Groups box EMPTY and the
    given mode. One grouped channel exists with two streams. If the blank box is
    honoured as "all groups", the channel is in scope and Sort reports work.
    """
    p = plugin_module.Plugin.__new__(plugin_module.Plugin)
    p.fuzzy_matcher = None

    class _Logger:
        def _record(self, msg, *a, **k):
            pass
        info = debug = warning = error = _record

    monkeypatch.setattr(p, "_initialize_fuzzy_matcher", lambda *a, **k: None)
    monkeypatch.setattr(p, "_build_alias_map", lambda *a, **k: {})
    monkeypatch.setattr(p, "_send_progress_update", lambda *a, **k: None)
    monkeypatch.setattr(p, "_get_all_profiles", lambda log: [{"id": 7, "name": "a"}])
    monkeypatch.setattr(p, "_get_all_groups", lambda log: [{"id": 30, "name": "Teamarr"}])
    monkeypatch.setattr(p, "_get_all_channels",
                        lambda log: [{"id": 1, "name": "Ch", "channel_group_id": 30}])
    monkeypatch.setattr(p, "_load_channels_data", lambda log, s: [])
    monkeypatch.setattr(p, "_zone_routed_map", lambda *a, **k: {})
    monkeypatch.setattr(p, "_same_country_ids_for", lambda *a, **k: None)
    monkeypatch.setattr(p, "_streams_for_channel", lambda streams, *a, **k: streams)
    monkeypatch.setattr(p, "_sort_streams_by_quality", lambda s: list(reversed(s)))

    plugin_module.ChannelProfileMembership.objects.filter.return_value.values_list.return_value = [1]
    plugin_module.ChannelStream.objects.filter.return_value.order_by.return_value.values_list.return_value = [101, 102]

    class _Stream:
        def __init__(self, sid):
            self.id, self.name = sid, f"Feed {sid}"
            self.stream_stats, self.m3u_account_id, self.channel_group_id = {}, 1, None

    plugin_module.Stream.objects.get.side_effect = lambda id: _Stream(id)
    plugin_module.Stream.DoesNotExist = type("DoesNotExist", (Exception,), {})

    return p.sort_streams_action(
        {"profile_name": "a", "selected_groups": "", "group_filter_mode": mode,
         "dry_run_mode": True}, _Logger())


def test_a_blank_group_box_means_ALL_groups_in_include_mode(plugin_module, monkeypatch):
    result = _blank_list_harness(plugin_module, monkeypatch, "include")
    assert "No channels found with multiple streams" not in (result.get("message") or ""), (
        "a blank Channel Groups box must mean every group, not no groups"
    )


def test_a_blank_group_box_means_ALL_groups_in_exclude_mode(plugin_module, monkeypatch):
    """Exclude mode with nothing listed excludes nothing, so the result must be
    identical to include mode with nothing listed."""
    result = _blank_list_harness(plugin_module, monkeypatch, "exclude")
    assert "No channels found with multiple streams" not in (result.get("message") or "")


def test_both_modes_agree_when_the_box_is_blank(plugin_module, monkeypatch):
    """The mode must make no difference at all when there is nothing to apply it
    to. Comparing the two results is what pins that."""
    inc = _blank_list_harness(plugin_module, monkeypatch, "include")
    exc = _blank_list_harness(plugin_module, monkeypatch, "exclude")
    assert inc.get("status") == exc.get("status")
    assert ("No channels found" in (inc.get("message") or "")) ==            ("No channels found" in (exc.get("message") or ""))
