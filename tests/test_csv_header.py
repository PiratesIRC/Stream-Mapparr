"""The preamble at the top of every CSV export, which is what a user actually reads.

Every report this plugin writes to /data/exports opens with about 45 commented
lines recording the settings the run used. Reviewed on 2026-09-05 against real
exports from that morning's scheduled runs, and three things were wrong.

TWO OF THE LINES WERE FALSE, not merely unclear.

  "Scheduled Times" read the settings key `schedule_cron`, which this plugin has
  never had. The real key is `scheduled_times`. So the line printed "(none)" on
  every export ever produced, including exports produced BY the scheduler. Both
  of the exports written on the morning of 5 September say "(none)" while the
  saved schedule held 05:05.

  "Execution Mode" was hardcoded to Manual in the sort action, with a comment
  claiming sorting is always manual. The scheduler calls that action, so both of
  that morning's scheduled sorts described themselves as Manual.

A preamble whose purpose is to record what produced the file below it is worse
than useless when it misreports that. Someone comparing two exports to work out
why the results differ is reading a record that is wrong in the two fields most
likely to explain the difference.

THE RESULT WAS NEVER STATED. The function accepts total_visible_channels and
total_matched_streams and used neither; both names appeared exactly once each, in
the signature. So the file said in detail how the run was configured and never
said what it did.

The rest of these tests cover plain wording: that the file explains what it is,
that settings read as on and off rather than as Python True and False, that the
match threshold says what the number means, and that nothing outside plain ASCII
reaches a file a spreadsheet may open under a different codepage.
"""
import ast
import io
import os

import pytest

PLUGIN_SOURCE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Stream-Mapparr", "plugin.py")


@pytest.fixture(scope="module")
def plugin_ast():
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        return ast.parse(handle.read())


def _plugin(plugin_module):
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    return inst


def _header(plugin_module, settings=None, processed=None, **kwargs):
    return _plugin(plugin_module)._generate_csv_header_comment(
        settings or {}, processed or {}, **kwargs)


def _line(header, prefix):
    for line in header.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"no line starting {prefix!r} in the header")


# --------------------------------------------------------------------------- #
# The two lines that were false
# --------------------------------------------------------------------------- #
def test_the_schedule_line_reports_the_schedule_that_is_configured(plugin_module):
    """It read a settings key this plugin has never had, so it always said none."""
    header = _header(plugin_module, {"scheduled_times": "0505"})
    assert "0505" in _line(header, "# Scheduled Times:")


def test_the_schedule_line_says_none_when_there_is_no_schedule(plugin_module):
    header = _header(plugin_module, {"scheduled_times": ""})
    assert "none" in _line(header, "# Scheduled Times:").lower()


def test_a_scheduled_run_does_not_describe_itself_as_manual(plugin_module):
    header = _header(plugin_module, is_scheduled=True)
    assert "Scheduled" in _line(header, "# Execution Mode:")


def test_a_manual_run_says_manual(plugin_module):
    header = _header(plugin_module, is_scheduled=False)
    assert "Manual" in _line(header, "# Execution Mode:")


def test_the_sort_action_passes_on_whether_it_was_scheduled(plugin_module):
    """The sort action hardcoded Manual, and the scheduler calls it.

    Reads the source, because the value has to travel from the action's argument
    into the header call, and a test on the header alone cannot see that.
    """
    with open(PLUGIN_SOURCE, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    func = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "sort_streams_action")
    assert any(a.arg == "is_scheduled" for a in func.args.args + func.args.kwonlyargs), \
        "sort_streams_action cannot report whether it was scheduled"
    for call in ast.walk(func):
        if (isinstance(call, ast.Call)
                and ast.unparse(call.func).endswith("_generate_csv_header_comment")):
            passed = {k.arg: ast.unparse(k.value) for k in call.keywords}
            assert passed.get("is_scheduled") == "is_scheduled", passed
            return
    raise AssertionError("sort_streams_action does not build a CSV header")


# --------------------------------------------------------------------------- #
# The result the run produced
# --------------------------------------------------------------------------- #
def test_the_header_states_what_the_run_actually_did(plugin_module):
    """Both counts were accepted as arguments and neither was ever printed."""
    header = _header(plugin_module, total_visible_channels=12, total_matched_streams=37)
    results = "\n".join(line for line in header.splitlines()
                        if line.startswith("# Channels") or line.startswith("# Streams"))
    assert "12" in results and "37" in results, header


def test_the_result_section_is_omitted_when_the_caller_cannot_supply_counts(plugin_module):
    """Two of the four callers do not have the numbers when the header is built.

    match_us_ota_only_action writes its CSV BEFORE the loop that assigns the
    streams, so the counts do not exist yet, and preview_changes_action does not
    pass them either. Printing 0 under a heading that reads "What This Run Did"
    states a measured fact that is false: a live OTA run assigning streams to
    hundreds of channels shipped a report claiming it changed none, directly
    above a table listing every one of those changes.

    Absent must therefore be distinguishable from zero, which a default of 0
    cannot do.
    """
    header = _header(plugin_module)
    assert "What This Run Did" not in header, header
    assert "Channels changed" not in header


def test_a_genuine_zero_is_still_reported(plugin_module):
    """A run that really did change nothing must say so, not fall silent."""
    header = _header(plugin_module, total_visible_channels=0, total_matched_streams=0)
    assert "What This Run Did" in header
    assert "# Channels changed: 0" in header


def test_no_caller_supplies_only_one_of_the_two_counts(plugin_ast):
    """Half the result is worse than none: the missing half reads as a zero."""
    offenders = []
    for node in ast.walk(plugin_ast):
        if isinstance(node, ast.FunctionDef):
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and ast.unparse(call.func).endswith("_generate_csv_header_comment")):
                    passed = {k.arg for k in call.keywords}
                    got = passed & {"total_visible_channels", "total_matched_streams"}
                    if len(got) == 1:
                        offenders.append((node.name, sorted(got)))
    assert offenders == [], offenders


# --------------------------------------------------------------------------- #
# The preamble has to name the setting the reader must go and change
# --------------------------------------------------------------------------- #
def _field_label(plugin_module, field_id):
    """The field's label with any leading icon and spacing removed."""
    inst = plugin_module.Plugin.__new__(plugin_module.Plugin)
    inst.version = "test"
    field = next(f for f in inst.fields if f.get("id") == field_id)
    return "".join(ch for ch in field["label"] if ord(ch) < 128).strip()


@pytest.mark.parametrize("field_id", [
    "scheduled_sort_streams", "scheduled_match_streams", "enable_scheduled_csv_export",
])
def test_the_preamble_uses_the_label_the_setting_has_in_the_interface(plugin_module, field_id):
    """Renaming a setting only in the report leaves the reader unable to find it.

    The rule was written into this file's own change and then broken by it: the
    preamble said "Schedule Runs Sort Alternate Streams" while the form said
    "Schedule: Sort Streams".
    """
    label = _field_label(plugin_module, field_id)
    header = _header(plugin_module)
    assert any(line.startswith("# " + label + ":") for line in header.splitlines()), \
        "no preamble line is titled %r" % label


def test_the_threshold_line_names_the_setting_that_controls_it(plugin_module):
    """The number is derived from a dropdown, and the dropdown is what to change."""
    line = _line(_header(plugin_module), "# Name Match Threshold:")
    assert "Match Sensitivity" in line, line


# --------------------------------------------------------------------------- #
# Plain wording
# --------------------------------------------------------------------------- #
def test_the_file_says_what_it_is_before_it_says_how_it_was_configured(plugin_module):
    """A user opening this in a spreadsheet has no other clue what they are looking at."""
    header = _header(plugin_module)
    top = header.splitlines()[:12]
    joined = " ".join(top).lower()
    assert "report" in joined or "export" in joined
    assert "hash" in joined, "the header never mentions the hash lines at all"
    assert any(word in joined for word in ("skip", "ignore", "not data", "explanation")), \
        "the header never explains that the hash lines are a preamble rather than data"


def test_settings_read_as_on_and_off_rather_than_python_booleans(plugin_module):
    header = _header(plugin_module, {"overwrite_streams": True, "prioritize_quality": False})
    assert "Yes" in _line(header, "# Overwrite Streams:")
    assert "No" in _line(header, "# Prioritize Quality Before Source:")
    assert "True" not in header and "False" not in header, \
        "raw Python booleans still reach the reader"


def test_a_setting_stored_as_a_string_still_reads_as_on_or_off(plugin_module):
    """Dispatcharr stores some booleans as the strings true and false."""
    header = _header(plugin_module, {"overwrite_streams": "true"})
    assert "Yes" in _line(header, "# Overwrite Streams:")


def test_the_match_threshold_says_what_the_number_means(plugin_module):
    """95 of what, and is higher stricter? The number alone answers neither."""
    line = _line(_header(plugin_module), "# Name Match Threshold:")
    assert "100" in line or "strict" in line.lower(), line


def test_the_header_is_plain_ascii(plugin_module):
    """A CSV may be opened by a spreadsheet under a different codepage.

    The recommendations section used an arrow character.
    """
    header = _header(plugin_module, total_visible_channels=1, total_matched_streams=1)
    bad = sorted({c for c in header if ord(c) > 127})
    assert not bad, [hex(ord(c)) for c in bad]


def test_every_preamble_line_is_commented(plugin_module):
    """One uncommented line would be read as data by a spreadsheet import."""
    header = _header(plugin_module)
    stray = [line for line in header.splitlines() if line and not line.startswith("#")]
    assert stray == [], stray
