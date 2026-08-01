"""Would an emailed report actually reach the inbox?

Newsflasharr's routing sends an event to `default_channels` unless a rule
matches it. So a plugin can spool successfully, see a delivery recorded, and
have the mail go somewhere other than email, with nothing anywhere reporting a
problem. This check exists to make that visible BEFORE a report is built.

Copied in shape from Dustarr, which already solved this.
"""


def _rules(*rules):
    import json
    return {"routing_rules": json.dumps(list(rules)), "default_channels": "apprise"}


def test_an_exact_source_and_event_rule_routes_to_smtp():
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp(_rules(
        {"match": {"source": "stream-mapparr", "event": "report"},
         "channels": ["smtp"]})) is True


def test_a_rule_for_another_plugin_does_not_count():
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp(_rules(
        {"match": {"source": "sentinelarr", "event": "weekly_report"},
         "channels": ["smtp"]})) is False


def test_a_rule_for_another_event_does_not_count():
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp(_rules(
        {"match": {"source": "stream-mapparr", "event": "something_else"},
         "channels": ["smtp"]})) is False


def test_a_wildcard_source_rule_counts():
    """A rule with no source matches every source."""
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp(_rules({"match": {"event": "report"}, "channels": ["smtp"]})) is True


def test_a_bare_source_rule_counts():
    """A rule with no event matches every event from that source."""
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp(_rules(
        {"match": {"source": "stream-mapparr"}, "channels": ["smtp"]})) is True


def test_smtp_in_the_default_channels_counts():
    """With no matching rule the event falls through to the defaults."""
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp({"routing_rules": "[]", "default_channels": "smtp"}) is True


def test_no_rule_and_no_smtp_default_is_false():
    """The silent failure this whole check exists to catch."""
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp({"routing_rules": "[]", "default_channels": "apprise"}) is False


def test_routing_rules_stored_as_a_list_is_accepted():
    """It is stored as a JSON string today. Accept a list in case that changes."""
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp({
        "routing_rules": [{"match": {"source": "stream-mapparr"}, "channels": ["smtp"]}],
        "default_channels": "apprise"}) is True


def test_malformed_routing_rules_do_not_raise():
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp({"routing_rules": "{not json", "default_channels": "apprise"}) is False
    assert routes_to_smtp({"routing_rules": None, "default_channels": None}) is False
    assert routes_to_smtp({}) is False


def test_a_non_dict_rule_is_skipped_rather_than_raising():
    from notify_bridge import routes_to_smtp
    assert routes_to_smtp({"routing_rules": '["nonsense", 42]',
                           "default_channels": "apprise"}) is False
