"""The stream country label check.

It compares two INDEPENDENT signals that already sit in the database: the
country prefix on a stream's group name, which the country restriction trusts,
and the two-letter suffix on its EPG identifier, which whoever built the EPG
wrote.

It exists because the only other way to verify a provider's country claim is to
open the stream and look at the picture, which spends one of a small number of
provider connections and can interrupt somebody watching. This costs two
database columns.

MEASURED on a live installation 2026-08-09: 3,500 streams carry both signals,
3,449 agree and 51 disagree. Reading the names, most disagreements are a channel
CARRIED in one country and MADE in another, which is ordinary. So this reports
and must never filter.
"""


def _plugin(plugin_module):
    P = plugin_module.Plugin
    inst = P.__new__(P)
    inst.version = "test"
    return inst


class _Logger:
    def _record(self, msg, *a, **k):
        pass
    info = debug = warning = error = _record


# --------------------------------------------------------------------------- #
# Reading the two signals
# --------------------------------------------------------------------------- #

def test_both_signals_are_read(plugin_module):
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "UK| NEWS", "tvg_id": "skynews.uk"}) == ("UK", "UK")


def test_the_pipe_and_colon_separators_are_both_accepted(plugin_module):
    """This provider uses both forms."""
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "US: SPORT", "tvg_id": "espn.us"})[0] == "US"


def test_a_missing_signal_is_reported_as_absent_not_as_a_disagreement(plugin_module):
    """Absent and disagreeing are different facts and the caller must be able to
    tell them apart. Treating unknown as a mismatch is how a check starts
    flagging thousands of ordinary streams."""
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "NEWS", "tvg_id": "skynews.uk"}) == (None, "UK")
    assert P._country_label_disagreement(
        {"channel_group__name": "UK| NEWS", "tvg_id": ""}) == ("UK", None)


def test_a_numeric_identifier_yields_no_suffix(plugin_module):
    """Many identifiers are bare numbers. A digit pair is not a country."""
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "UK| NEWS", "tvg_id": "12345"})[1] is None


def test_a_longer_tail_is_not_mistaken_for_a_country(plugin_module):
    """Exactly two letters, anchored to the end."""
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "UK| NEWS", "tvg_id": "channel.info"})[1] is None


def test_a_multi_part_domain_uses_the_final_label(plugin_module):
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "UK| NEWS", "tvg_id": "bbc.co.uk"})[1] == "UK"


def test_case_is_normalised_on_both_sides(plugin_module):
    P = plugin_module.Plugin
    assert P._country_label_disagreement(
        {"channel_group__name": "uk| news", "tvg_id": "SKYNEWS.UK"}) == ("UK", "UK")


def test_a_non_dict_does_not_raise(plugin_module):
    P = plugin_module.Plugin
    assert P._country_label_disagreement(None) == (None, None)


# --------------------------------------------------------------------------- #
# The action
# --------------------------------------------------------------------------- #

def _run(plugin_module, tmp_path, streams):
    inst = _plugin(plugin_module)
    inst.BUG_REPORT_DIR = str(tmp_path)
    inst._get_all_streams = lambda logger: streams
    return inst.check_stream_countries_action({}, _Logger())


def test_it_counts_agreements_and_disagreements(plugin_module, tmp_path):
    out = _run(plugin_module, tmp_path, [
        {"name": "a", "channel_group__name": "UK| NEWS", "tvg_id": "one.uk"},
        {"name": "b", "channel_group__name": "UK| NEWS", "tvg_id": "two.uk"},
        {"name": "c", "channel_group__name": "UK| NEWS", "tvg_id": "three.us"},
    ])
    assert out["status"] == "success"
    assert "2 of 3" in out["message"]
    assert "1 disagree" in out["message"]


def test_streams_missing_a_signal_are_excluded_from_the_denominator(plugin_module, tmp_path):
    """Otherwise the agreement rate silently measures coverage instead."""
    out = _run(plugin_module, tmp_path, [
        {"name": "a", "channel_group__name": "UK| NEWS", "tvg_id": "one.uk"},
        {"name": "b", "channel_group__name": "NEWS", "tvg_id": "two.uk"},
        {"name": "c", "channel_group__name": "UK| NEWS", "tvg_id": ""},
    ])
    assert "1 of 1" in out["message"]


def test_nothing_comparable_says_so_rather_than_claiming_success(plugin_module, tmp_path):
    """A rate computed from zero comparisons would read as a clean bill of
    health when in fact nothing was measured."""
    out = _run(plugin_module, tmp_path, [
        {"name": "a", "channel_group__name": "NEWS", "tvg_id": "12345"},
    ])
    assert "nothing to compare" in out["message"]
    assert "100" not in out["message"]


def test_it_writes_a_file_and_returns_its_path(plugin_module, tmp_path):
    """A toast clips at roughly 280 characters, so a per-stream list cannot live
    in the message."""
    out = _run(plugin_module, tmp_path, [
        {"name": "Al Jazeera", "channel_group__name": "US| NEWS", "tvg_id": "aljazeera.qa"},
    ])
    assert "file" in out
    text = open(out["file"], encoding="utf-8").read()
    assert "Al Jazeera" in text
    assert "US" in text and "QA" in text


def test_the_file_says_a_disagreement_is_not_automatically_a_fault(plugin_module, tmp_path):
    """Measured: most disagreements are a channel carried in one country and made
    in another. A report that reads as an error list invites deleting them."""
    out = _run(plugin_module, tmp_path, [
        {"name": "x", "channel_group__name": "US| NEWS", "tvg_id": "x.qa"},
    ])
    text = open(out["file"], encoding="utf-8").read().lower()
    assert "not automatically a fault" in text


def test_a_failure_to_load_streams_is_reported_as_an_error(plugin_module, tmp_path):
    inst = _plugin(plugin_module)
    inst.BUG_REPORT_DIR = str(tmp_path)

    def _boom(logger):
        raise RuntimeError("no database")

    inst._get_all_streams = _boom
    out = inst.check_stream_countries_action({}, _Logger())
    assert out["status"] == "error"
    assert "error" in out


def test_the_action_never_reports_success_with_only_an_error_key(plugin_module, tmp_path):
    """status renders nowhere in Dispatcharr's plugin card. Exactly one of
    message or error must be set, or a failure looks identical to a success."""
    out = _run(plugin_module, tmp_path, [
        {"name": "a", "channel_group__name": "UK| NEWS", "tvg_id": "one.uk"},
    ])
    assert ("message" in out) != ("error" in out)
