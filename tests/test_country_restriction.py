"""End-to-end tests for the country restriction filter (bug-158, bug-159)."""

import logging

LOGGER = logging.getLogger("test_country_restriction")


def test_resolve_restrict_coerces_string_false(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    assert inst._resolve_restrict_matching_to_country({"restrict_matching_to_country": "false"}) is False
    assert inst._resolve_restrict_matching_to_country({"restrict_matching_to_country": "true"}) is True
    assert inst._resolve_restrict_matching_to_country({"restrict_matching_to_country": True}) is True
    assert inst._resolve_restrict_matching_to_country({}) is False


def test_both_actions_load_before_reading_processed_data(plugin_module):
    """Spec §10: reading the flag from processed_data is safe only because both
    consuming actions call load_process_channels_action FIRST with live settings.
    That is an emergent property of two call sites, not an enforced invariant —
    this is the lock (bug-139 family)."""
    import inspect
    for action in ("preview_changes_action", "add_streams_to_channels_action"):
        src = inspect.getsource(getattr(plugin_module.Plugin, action))
        load_at = src.index("load_process_channels_action")
        # NB: the read spans a line break in the source —
        #     processed_data.get(\n    'restrict_matching_to_country', ...)
        # so match the quoted literal, not the whole call expression.
        read_at = src.index("'restrict_matching_to_country'")
        assert load_at < read_at, f"{action} reads the flag before loading channels"


def _inst(plugin_module):
    P = plugin_module.Plugin
    return P.__new__(P)


def test_channel_country_prefers_channel_database_entry(plugin_module):
    """bug-158: the channel DATABASE is the strongest signal. CNN/TNT/USA Network
    have no usable country marker in their own name or group, which is why the
    reporter's three rows all leaked."""
    inst = _inst(plugin_module)
    channel = {"name": "CNN", "channel_group__name": "News"}
    assert inst._channel_country_code(channel, {"channel_name": "CNN", "_country_code": "US"}) == "US"


def test_channel_country_falls_back_to_group_then_name(plugin_module):
    inst = _inst(plugin_module)
    assert inst._channel_country_code({"name": "CNN", "channel_group__name": "USA: News"}, None) == "US"
    assert inst._channel_country_code({"name": "UK: CNN", "channel_group__name": "News"}, None) == "UK"
    assert inst._channel_country_code({"name": "CNN", "channel_group__name": "News"}, None) is None


def test_channel_country_bare_group_us(plugin_module):
    """The reporter's probe row 2 — a group named exactly 'US'."""
    inst = _inst(plugin_module)
    assert inst._channel_country_code({"name": "CNN", "channel_group__name": "US"}, None) == "US"


def test_stream_country_group_first_then_name(plugin_module):
    inst = _inst(plugin_module)
    # This box's real group shape; every GO:/RK: stream lives in one of these.
    assert inst._stream_country_code(
        {"name": "GO: ESPN", "channel_group__name": "US| DIREC TV"}) == "US"
    assert inst._stream_country_code(
        {"name": "UK: CNN", "channel_group__name": "News"}) == "UK"
    # Unrecognized platform tag with no group signal -> UNKNOWN, must be kept.
    assert inst._stream_country_code({"name": "RK: DAZN RINGSIDE", "channel_group__name": None}) is None


def test_stream_country_reads_raw_name_not_match_name(plugin_module):
    """issue #36 consumer split: a regex rule that strips a country prefix for
    matching must not blind country detection."""
    inst = _inst(plugin_module)
    stream = {"name": "UK: CNN", "match_name": "CNN", "channel_group__name": None}
    assert inst._stream_country_code(stream) == "UK"
