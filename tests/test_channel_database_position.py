"""Where the Channel Database setting sits on the settings form.

It used to be appended last, below the throughput probe settings. That is the
wrong place for it: it decides which country's channel list loads, and that list
is the primary signal for a channel's country, which is what Restrict Matching
To Same Country filters on. Measured on a live installation, the setting had
never been touched, so it fell back to the US list while the operator was
processing a UK channel group. Enabling the country filter then classified 34 UK
channels as American and dropped their UK streams as foreign, emptying 13
channels entirely.

These tests lock it to the scope settings at the top of the form. They fail if
it drifts back down.
"""


def _fields(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst.fields


def _ids(plugin_module):
    return [f.get("id") for f in _fields(plugin_module)]


def _database_field_id(plugin_module):
    """Whichever of the three the build produced: the real dropdown, or one of
    the two information panels shown when the databases cannot be listed."""
    ids = _ids(plugin_module)
    for candidate in ("channel_database", "no_databases_found", "database_error"):
        if candidate in ids:
            return candidate
    raise AssertionError("no channel-database field of any kind is served")


def test_channel_database_is_served(plugin_module):
    assert _database_field_id(plugin_module) == "channel_database"


def test_channel_database_sits_immediately_before_channel_profile(plugin_module):
    ids = _ids(plugin_module)
    assert ids.index("channel_database") + 1 == ids.index("profile_name")


def test_channel_database_is_near_the_top_of_the_form(plugin_module):
    """A number rather than a name, because the failure being guarded against is
    the operator never scrolling far enough to see it."""
    ids = _ids(plugin_module)
    assert ids.index("channel_database") < 8


def test_channel_database_precedes_the_country_filter_it_feeds(plugin_module):
    ids = _ids(plugin_module)
    assert ids.index("channel_database") < ids.index("restrict_matching_to_country")


def test_channel_database_precedes_the_probe_settings_it_used_to_follow(plugin_module):
    ids = _ids(plugin_module)
    for later in ("enable_throughput_sorting", "content_bitrate_floor_kbps"):
        assert ids.index("channel_database") < ids.index(later)


def test_the_help_text_says_what_the_setting_actually_decides(plugin_module):
    """The old help text described it as a database for name matching and never
    mentioned the country filter, which is the connection that cost real time."""
    field = next(f for f in _fields(plugin_module) if f.get("id") == "channel_database")
    help_text = field.get("help_text", "").lower()
    assert "country" in help_text


def test_no_field_id_appears_twice(plugin_module):
    """Inserting into the list rather than appending to it makes a duplicate id
    possible in a way appending never did."""
    ids = [i for i in _ids(plugin_module) if i]
    assert len(ids) == len(set(ids))
