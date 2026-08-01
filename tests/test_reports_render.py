"""Rendering the report model to HTML and CSV, and writing both to disk.

The HTML is styled to match Dustarr's report. The CSV is a second attachment
rather than a reuse of the exports in /data/exports, because those carry the M3U
account name appended to every stream name.
"""
import os
import tempfile

ACCOUNTS = ["streamq.tv", "streamq.tv-bk15"]


def _model(now=1785237435.0):
    from reports import build_model
    return build_model(
        [{"channel_name": "Sky News", "stream_names": ["SKY NEWS HD", "SKY NEWS FHD"]},
         {"channel_name": "US: ABC 45 HD", "stream_names": ["US: ABC 45 HD [WINSTON-SALEM]"]}],
        ACCOUNTS, {}, now)


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

def test_html_escapes_a_channel_name():
    """Channel and stream names are provider-supplied text and reach the page
    verbatim otherwise."""
    from reports import build_model, render_html
    html = render_html(build_model(
        [{"channel_name": "<script>alert(1)</script>", "stream_names": []}],
        ACCOUNTS, {}, 0))
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_html_escapes_a_stream_name():
    from reports import build_model, render_html
    html = render_html(build_model(
        [{"channel_name": "ok", "stream_names": ["<img src=x onerror=1>"]}],
        ACCOUNTS, {}, 0))
    assert "<img src=x" not in html


def test_html_is_self_contained():
    """No external stylesheet, script or image. The page is read from a file
    path or inside an email client, where an external request would not
    resolve and would leak a read receipt."""
    from reports import render_html
    html = render_html(_model())
    for marker in ("http://", "https://", "<link ", "<script"):
        assert marker not in html


def test_html_carries_the_channel_and_stream_names():
    from reports import render_html
    html = render_html(_model())
    assert "Sky News" in html
    assert "SKY NEWS FHD" in html
    assert "[WINSTON-SALEM]" in html


def test_html_reports_the_channel_count():
    from reports import render_html
    assert "2" in render_html(_model())


def test_html_renders_an_empty_model_without_raising():
    from reports import build_model, render_html
    html = render_html(build_model([], ACCOUNTS, {}, 0))
    assert "<html" in html.lower() or "<!doctype" in html.lower()


# --------------------------------------------------------------------------- #
# CSV
# --------------------------------------------------------------------------- #

def test_csv_has_a_header_row():
    from reports import render_csv
    first = render_csv(_model()).splitlines()[0]
    assert "channel_name" in first
    assert "stream_names" in first


def test_csv_quotes_a_value_containing_a_comma():
    from reports import build_model, render_csv
    text = render_csv(build_model(
        [{"channel_name": "News, Sport", "stream_names": []}], ACCOUNTS, {}, 0))
    assert '"News, Sport"' in text


def test_csv_neutralises_a_formula_injection():
    """A leading =, +, - or @ makes a spreadsheet evaluate the cell when the
    file is opened."""
    from reports import build_model, render_csv
    text = render_csv(build_model(
        [{"channel_name": "=cmd|'/c calc'!A1", "stream_names": []}], ACCOUNTS, {}, 0))
    assert "'=cmd" in text


def test_csv_neutralises_every_dangerous_leading_character():
    from reports import build_model, render_csv
    for lead in ("=", "+", "-", "@"):
        text = render_csv(build_model(
            [{"channel_name": lead + "danger", "stream_names": []}], ACCOUNTS, {}, 0))
        assert "'" + lead + "danger" in text


# --------------------------------------------------------------------------- #
# Writing to disk
# --------------------------------------------------------------------------- #

def test_write_report_returns_both_paths():
    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        written = write_report(_model(), d, 1785237435.0)
        assert written["error"] is None
        assert os.path.isfile(written["html_path"])
        assert os.path.isfile(written["csv_path"])


def test_both_files_are_timestamped_and_never_rewritten():
    """An SMTP send re-reads the attachment path on every retry attempt, so each
    run must produce new files rather than rewriting one in place."""
    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        a = write_report(_model(), d, 1785237435.0)
        b = write_report(_model(), d, 1785240000.0)
        assert a["html_path"] != b["html_path"]
        assert a["csv_path"] != b["csv_path"]
        assert os.path.isfile(a["html_path"])


def test_no_temporary_file_is_left_behind():
    """The write is atomic, so no partial file is ever visible at the
    destination and no stray temporary survives."""
    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        write_report(_model(), d, 1785237435.0)
        assert [f for f in os.listdir(d) if ".tmp" in f] == []


def test_pruning_keeps_at_least_eight_of_each_type():
    """Retention must exceed the SMTP retry ladder's worst case of 2130 seconds
    by a wide margin, because Newsflasharr re-reads the path on every attempt."""
    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        for i in range(14):
            write_report(_model(), d, 1785237435.0 + i * 3600)
        html = [f for f in os.listdir(d) if f.endswith(".html")]
        csv = [f for f in os.listdir(d) if f.endswith(".csv")]
        assert len(html) == 8
        assert len(csv) == 8


def test_pruning_keeps_the_NEWEST_files():
    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        paths = [write_report(_model(), d, 1785237435.0 + i * 3600)["html_path"]
                 for i in range(12)]
        assert os.path.isfile(paths[-1]), "the newest report must survive pruning"
        assert not os.path.isfile(paths[0]), "the oldest report must be pruned"


def test_write_report_reports_its_own_failure_rather_than_raising():
    """Uses an existing FILE as the report directory, which cannot be created as
    a directory on any platform. A merely absent path is not a failure case:
    os.makedirs builds the whole chain."""
    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        blocker = os.path.join(d, "iam_a_file")
        with open(blocker, "w", encoding="utf-8") as f:
            f.write("x")
        written = write_report(_model(), os.path.join(blocker, "reports"), 0)
    assert written["html_path"] is None
    assert written["csv_path"] is None
    assert written["error"]


def test_the_report_directory_is_not_under_the_lan_exposed_logos_tree():
    """Dispatcharr's nginx serves /data/logos unauthenticated to the whole
    network. Reports must not be written there."""
    from reports import REPORT_DIR
    assert "/logos" not in REPORT_DIR


def test_the_table_headers_match_their_columns():
    """The middle column holds the match COUNT and the last holds the stream
    list. An earlier version labelled them the other way round, which the other
    tests did not catch because they only checked that content was present."""
    from reports import render_html
    html = render_html(_model())
    header = html.split("<tr>")[1].split("</tr>")[0]
    assert header.index("Channel") < header.index("Matched") < header.index("Streams")
