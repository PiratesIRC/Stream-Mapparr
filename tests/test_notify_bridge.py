"""Settings and emit path for the Newsflasharr notification service.

Two settings, not one. `notify_enabled` is the master switch the Newsflasharr
integration template mandates verbatim, and its default of False is a template
invariant rather than a style choice: a released plugin must never start writing
into another plugin's spool directory the moment it upgrades. `notify_report_on`
is the caller-side filter the template sanctions for a caller with more than one
emission trigger.
"""


def _fields(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst.fields


def _field(plugin_module, field_id):
    return next((f for f in _fields(plugin_module) if f.get("id") == field_id), None)


# --------------------------------------------------------------------------- #
# The master toggle
# --------------------------------------------------------------------------- #

def test_notify_enabled_field_is_served(plugin_module):
    assert _field(plugin_module, "notify_enabled") is not None


def test_notify_enabled_defaults_to_off(plugin_module):
    """A released plugin must never start writing into another plugin's spool
    directory the moment it upgrades. The operator opts in."""
    assert _field(plugin_module, "notify_enabled")["default"] is False


def test_notify_enabled_is_a_boolean(plugin_module):
    assert _field(plugin_module, "notify_enabled")["type"] == "boolean"


# --------------------------------------------------------------------------- #
# The report trigger dropdown
# --------------------------------------------------------------------------- #

def test_report_trigger_is_a_dropdown(plugin_module):
    assert _field(plugin_module, "notify_report_on")["type"] == "select"


def test_report_trigger_offers_exactly_three_choices(plugin_module):
    offered = {o["value"] for o in _field(plugin_module, "notify_report_on")["options"]}
    assert offered == {"never", "scheduled", "every_run"}


def test_report_trigger_defaults_to_scheduled_runs_only(plugin_module):
    assert _field(plugin_module, "notify_report_on")["default"] == "scheduled"


def test_report_trigger_option_labels_say_what_each_does(plugin_module):
    labels = {o["value"]: o["label"].lower()
              for o in _field(plugin_module, "notify_report_on")["options"]}
    assert "never" in labels["never"]
    assert "scheduled" in labels["scheduled"]
    assert "every" in labels["every_run"]


def test_the_dropdown_sits_directly_below_the_master_toggle(plugin_module):
    """Order matters in the rendered form: a selector separated from the toggle
    it depends on reads as applying to whatever is next to it."""
    ids = [f.get("id") for f in _fields(plugin_module)]
    assert ids.index("notify_report_on") == ids.index("notify_enabled") + 1


def test_every_offered_trigger_is_one_the_resolver_understands(plugin_module):
    """A dropdown value the resolver does not recognise would silently fall back
    to the default, so the form could offer a mode that does nothing."""
    offered = {o["value"] for o in _field(plugin_module, "notify_report_on")["options"]}
    assert offered == set(plugin_module.PluginConfig.NOTIFY_REPORT_TRIGGERS)


def test_no_em_dashes_in_the_new_field_copy(plugin_module):
    """Standing rule for this workspace: no em dashes in plugin-facing copy."""
    for field_id in ("notify_enabled", "notify_report_on"):
        field = _field(plugin_module, field_id)
        blob = " ".join([
            str(field.get("label", "")),
            str(field.get("help_text", "")),
            " ".join(o.get("label", "") for o in field.get("options", [])),
        ])
        assert "—" not in blob


# --------------------------------------------------------------------------- #
# The report format dropdown
# --------------------------------------------------------------------------- #

def test_report_format_is_a_dropdown(plugin_module):
    assert _field(plugin_module, "notify_report_format")["type"] == "select"


def test_report_format_offers_exactly_three_choices(plugin_module):
    offered = {o["value"] for o in _field(plugin_module, "notify_report_format")["options"]}
    assert offered == {"html", "csv", "both"}


def test_report_format_defaults_to_both(plugin_module):
    assert _field(plugin_module, "notify_report_format")["default"] == "both"


def test_every_offered_format_is_one_the_resolver_understands(plugin_module):
    offered = {o["value"] for o in _field(plugin_module, "notify_report_format")["options"]}
    assert offered == set(plugin_module.PluginConfig.NOTIFY_REPORT_FORMATS)


def test_the_format_field_sits_directly_below_the_trigger(plugin_module):
    ids = [f.get("id") for f in _fields(plugin_module)]
    assert ids.index("notify_report_format") == ids.index("notify_report_on") + 1


def test_the_format_labels_say_how_many_emails_arrive(plugin_module):
    """A notification carries one attachment, so choosing both means two
    separate emails. The operator should not have to discover that."""
    labels = " ".join(o["label"].lower()
                      for o in _field(plugin_module, "notify_report_format")["options"])
    assert "two" in labels or "2" in labels


def test_no_em_dashes_in_the_format_field_copy(plugin_module):
    field = _field(plugin_module, "notify_report_format")
    blob = " ".join([str(field.get("label", "")), str(field.get("help_text", "")),
                     " ".join(o.get("label", "") for o in field.get("options", []))])
    assert "—" not in blob
