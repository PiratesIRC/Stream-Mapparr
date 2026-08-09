"""A field's declared type must match the control the operator is given.

MEASURED by reading Dispatcharr's own plugin settings form in the running
frontend bundle: the switch on a field's type builds a Mantine Textarea for
"text", and every other value falls through to the default branch, which builds
a single-line TextInput. So:

    "type": "text"    ->  multi-line box, newlines can be typed
    "type": "string"  ->  ONE LINE, a newline cannot be entered at all

The defect this locks out: a field labelled "one per line" or asking for JSON
while declared as "string". The label then describes something the input
physically cannot accept. Found 2026-08-09 when an operator asked how to enter
one entry per line into a single-line box, and it affected six fields at once,
three of which had arrived days earlier and had never been used.

A field may legitimately be single-line and comma separated. What it may not be
is single-line while TELLING the operator to use lines.
"""
import re

MULTILINE_TYPE = "text"

# Wording that promises the operator more than one line.
PROMISES_LINES = re.compile(r"per line|one per line|each on its own line|newline",
                            re.IGNORECASE)
# Content that is unusable on one line in practice.
PROMISES_JSON = re.compile(r"\bJSON\b")


def _fields(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst.fields


def _text_of(field):
    return " ".join(str(field.get(k, "")) for k in ("label", "help_text", "placeholder"))


def test_no_field_promises_lines_while_being_single_line(plugin_module):
    offenders = []
    for field in _fields(plugin_module):
        if field.get("type") in ("info", "boolean", "number", "select"):
            continue
        if PROMISES_LINES.search(_text_of(field)) and field.get("type") != MULTILINE_TYPE:
            offenders.append(field.get("id"))
    assert not offenders, (
        "these fields tell the operator to use one entry per line but render as a "
        "single-line input, where a newline cannot be typed: %s" % offenders)


def test_no_field_asks_for_json_while_being_single_line(plugin_module):
    offenders = []
    for field in _fields(plugin_module):
        if field.get("type") in ("info", "boolean", "number", "select"):
            continue
        if PROMISES_JSON.search(_text_of(field)) and field.get("type") != MULTILINE_TYPE:
            offenders.append(field.get("id"))
    assert not offenders, (
        "these fields hold JSON but render as a single-line input: %s" % offenders)


def test_the_five_known_multiline_fields_are_declared_as_such(plugin_module):
    """Named explicitly so that a future edit flipping one back is caught even if
    its wording changed at the same time."""
    expected = {
        "custom_aliases",
        "stream_name_regex_rules",
        "epg_placeholder_name_patterns",
        "epg_title_cleanup_rules",
        "epg_channel_schedule_cleanup_rules",
    }
    by_id = {f.get("id"): f for f in _fields(plugin_module)}
    for field_id in expected:
        assert field_id in by_id, "%s is no longer served" % field_id
        assert by_id[field_id]["type"] == MULTILINE_TYPE, field_id


def test_the_prefix_country_field_stays_single_line_and_says_so(plugin_module):
    """This one is deliberately NOT multi-line, so its wording must offer the
    comma form rather than lines."""
    field = next(f for f in _fields(plugin_module)
                 if f.get("id") == "stream_prefix_countries")
    assert field["type"] == "string"
    assert "comma" in _text_of(field).lower()
    assert not PROMISES_LINES.search(_text_of(field))


def test_the_declared_type_is_one_dispatcharr_accepts(plugin_module):
    """A field whose type fails validation is dropped SILENTLY, with only a
    warning in the log, and the setting simply never appears."""
    allowed = {"string", "number", "boolean", "select", "text", "info"}
    for field in _fields(plugin_module):
        assert field.get("type") in allowed, (field.get("id"), field.get("type"))
