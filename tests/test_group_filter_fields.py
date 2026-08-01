"""The settings fields that expose the channel-group and stream-group filter mode.

The mode is a two-option dropdown rather than a checkbox on purpose. A checkbox
labelled "Exclude" does not say what leaving it unticked means, and the two
meanings are opposites: one processes only the groups listed, the other processes
everything except them. A dropdown states both.
"""


def _fields(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst.fields


def _field(plugin_module, field_id):
    for f in _fields(plugin_module):
        if f.get("id") == field_id:
            return f
    return None


def _ids(plugin_module):
    return [f.get("id") for f in _fields(plugin_module)]


def test_channel_group_mode_field_is_served(plugin_module):
    assert _field(plugin_module, "group_filter_mode") is not None


def test_stream_group_mode_field_is_served(plugin_module):
    assert _field(plugin_module, "stream_group_filter_mode") is not None


def test_mode_fields_are_dropdowns_not_checkboxes(plugin_module):
    for field_id in ("group_filter_mode", "stream_group_filter_mode"):
        assert _field(plugin_module, field_id)["type"] == "select"


def test_mode_fields_default_to_include(plugin_module):
    """An installation that never touches these settings must behave exactly as
    it did before they existed."""
    for field_id in ("group_filter_mode", "stream_group_filter_mode"):
        assert _field(plugin_module, field_id)["default"] == "include"


def test_every_offered_option_is_one_the_resolver_understands(plugin_module):
    """A dropdown value the resolver does not recognize would silently fall back
    to include, so the form could offer a mode that does nothing."""
    valid = set(plugin_module.PluginConfig.GROUP_FILTER_MODES)
    for field_id in ("group_filter_mode", "stream_group_filter_mode"):
        offered = {o["value"] for o in _field(plugin_module, field_id)["options"]}
        assert offered == valid


def test_mode_field_sits_directly_below_the_list_it_modifies(plugin_module):
    """Order matters in the rendered form: a mode selector separated from its
    text field reads as applying to whatever is next to it."""
    ids = _ids(plugin_module)
    assert ids.index("group_filter_mode") == ids.index("selected_groups") + 1
    assert ids.index("stream_group_filter_mode") == ids.index("selected_stream_groups") + 1


def test_option_labels_say_what_each_mode_does(plugin_module):
    """The labels carry the whole meaning, so they must not be bare words like
    "include" and "exclude"."""
    for field_id in ("group_filter_mode", "stream_group_filter_mode"):
        labels = {o["value"]: o["label"] for o in _field(plugin_module, field_id)["options"]}
        assert "only" in labels["include"].lower()
        assert "except" in labels["exclude"].lower()


def test_no_em_dashes_in_the_new_field_copy(plugin_module):
    """Standing rule for this workspace: no em dashes in plugin-facing copy."""
    for field_id in ("group_filter_mode", "stream_group_filter_mode"):
        field = _field(plugin_module, field_id)
        blob = " ".join([
            str(field.get("label", "")),
            str(field.get("help_text", "")),
            " ".join(o.get("label", "") for o in field.get("options", [])),
        ])
        assert "—" not in blob


def test_m3u_source_list_has_no_mode_field(plugin_module):
    """Deliberately absent. That list's ORDER sets source priority, and an
    exclusion list leaves no way to express an order for the sources that remain,
    so every one of them would tie at the default priority and the source
    priority sort would silently stop having any effect."""
    assert "m3u_filter_mode" not in _ids(plugin_module)
