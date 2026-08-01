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


def _age(path, seconds):
    """Backdate a file so the pruning age guard considers it safe to delete."""
    import time
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_pruning_keeps_eight_of_each_type_once_files_are_old_enough():
    """Files are backdated past the delivery retry window first. A freshly
    written file is never pruned however many newer ones exist, which is what
    test_pruning_never_deletes_a_file_still_inside_the_retry_window covers."""
    from reports import RETRY_WINDOW_SECONDS, write_report
    with tempfile.TemporaryDirectory() as d:
        for i in range(14):
            written = write_report(_model(), d, 1785237435.0 + i * 3600)
            _age(written["html_path"], RETRY_WINDOW_SECONDS + 3600 - i)
            _age(written["csv_path"], RETRY_WINDOW_SECONDS + 3600 - i)
        write_report(_model(), d, 1785237435.0 + 99 * 3600)
        html = [f for f in os.listdir(d) if f.endswith(".html")]
        csv = [f for f in os.listdir(d) if f.endswith(".csv")]
        assert len(html) <= 9
        assert len(csv) <= 9


def test_pruning_keeps_the_NEWEST_files():
    from reports import RETRY_WINDOW_SECONDS, write_report
    with tempfile.TemporaryDirectory() as d:
        paths = []
        for i in range(12):
            written = write_report(_model(), d, 1785237435.0 + i * 3600)
            paths.append(written["html_path"])
            _age(written["html_path"], RETRY_WINDOW_SECONDS + 3600 - i)
            _age(written["csv_path"], RETRY_WINDOW_SECONDS + 3600 - i)
        newest = write_report(_model(), d, 1785237435.0 + 99 * 3600)
        assert os.path.isfile(newest["html_path"]), "the newest report must survive"
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


# --------------------------------------------------------------------------- #
# Gap found by a correctness review of the written code, 2026-08-01
# --------------------------------------------------------------------------- #

def test_pruning_never_deletes_a_file_still_inside_the_retry_window():
    """Newsflasharr re-reads an attachment path on every retry attempt across a
    worst case of 2130 seconds, about 35 minutes. Deleting a file inside that
    window strips the attachment from mail already queued.

    A count-only rule is not enough: the event-driven auto-match fires once per
    M3U account on an M3U refresh, so a burst can produce several report pairs
    in minutes and push older ones past the keep count while they are still
    being retried.
    """
    import time

    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        paths = [write_report(_model(), d, now + i)["html_path"] for i in range(14)]
        # Every file was written seconds ago, so every one is inside the window.
        assert all(os.path.isfile(p) for p in paths), (
            "no recently written report may be pruned while a retry could still "
            "need it"
        )


def test_pruning_still_removes_files_older_than_the_retry_window():
    """The age guard must not turn pruning off altogether."""
    import os as _os
    import time

    from reports import write_report
    with tempfile.TemporaryDirectory() as d:
        old_paths = [write_report(_model(), d, 1785237435.0 + i)["html_path"]
                     for i in range(12)]
        stale = time.time() - 86400
        for p in old_paths:
            _os.utime(p, (stale, stale))
        write_report(_model(), d, time.time())
        remaining = [f for f in _os.listdir(d) if f.endswith(".html")]
        assert len(remaining) <= 9, remaining


def test_the_retry_window_constant_exceeds_the_documented_ladder():
    """30s + 300s + 1800s = 2130s is the documented worst case."""
    from reports import RETRY_WINDOW_SECONDS
    assert RETRY_WINDOW_SECONDS >= 2130
