"""Report model and rendering for Stream-Mapparr.

The model is built by COPYING a known set of keys rather than by filtering an
unknown one. Newsflasharr sends an attachment verbatim and unredacted, so
anything reaching this model can leave the box by email, and a column added to
an export later must not be able to start being emailed on its own.

Two measured facts about this installation drive the design:

1. Every CSV export in /data/exports appends the M3U account name to each stream
   name, and on this box those account names are the provider's hostnames.
2. 327 stream names contain square brackets and not one of them holds an account
   name. The brackets hold the market, which for an over-the-air station is its
   whole identity.

So the primary defence is that build_model is fed RAW stream names, which never
carried an account label, and sanitise_stream_label removes an EXACT account
name only, as a backstop.
"""
import csv
import datetime
import html
import io
import os
import re

# Reports are written here, deliberately NOT under /data/logos: Dispatcharr's
# nginx serves that tree unauthenticated to the entire local network.
REPORT_DIR = "/data/stream_mapparr_reports"

# How many of each file type to keep. Newsflasharr re-reads an attachment path
# on every retry attempt across a worst case of 2130 seconds, about 35 minutes,
# so retention must exceed that by a wide margin. At one report per run, eight
# is days of headroom.
KEEP_REPORTS = 8

# Matches a bare IPv4 address. The Sort export carries provider edge server
# addresses in a column of its own; this catches one reaching a name field by
# any other route.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def sanitise_stream_label(label, account_names):
    """Remove an M3U account name in brackets, and nothing else.

    An unknown bracketed value is left alone, because on this installation those
    hold the market rather than a source label. Removing every bracketed group
    would strip nothing that leaks and would collapse dozens of distinct
    over-the-air stations into one indistinguishable name.

    Account names are matched longest first: "streamq.tv" is a prefix of
    "streamq.tv-bk15", and matching the shorter one first would leave a
    "-bk15]" fragment behind.
    """
    text = str(label or "")
    for account in sorted([a for a in (account_names or []) if a], key=len, reverse=True):
        text = re.sub(r"\s*\[" + re.escape(account) + r"\]", "", text)
    return text.strip()


def _scrub(value, account_names):
    """Apply every content rule to one free-text value."""
    cleaned = sanitise_stream_label(value, account_names)
    return _IPV4_RE.sub("", cleaned).strip()


def build_model(channels, account_names, settings, now):
    """Build the report model from a list of per-channel result dicts.

    Only the keys named below are copied. Anything else the caller passes is
    dropped rather than carried through.

    `stream_names` must be a list. A caller that passes the semicolon-joined
    string the CSV export builds would otherwise have it iterated one character
    at a time, producing a report of single letters, so a string raises instead
    of being silently mangled.
    """
    entries = []
    for row in channels or []:
        raw = row.get("stream_names") or []
        if isinstance(raw, str):
            raise TypeError(
                "stream_names must be a list, not the joined string the CSV "
                "export builds. Pass the raw matched stream names.")
        names = [_scrub(s, account_names) for s in raw]
        entries.append({
            "channel_name": _scrub(row.get("channel_name"), account_names),
            "matched": int(row.get("matched") or len(names)),
            "stream_names": names,
        })
    return {
        "generated_ts": float(now or 0),
        "channel_count": len(entries),
        "entries": entries,
    }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

# Styling is inlined because the page is read from a file path or inside an
# email client, where an external stylesheet would not resolve. Colours and
# layout follow Dustarr's report so the two look like one family.
_CSS = """
:root { color-scheme: light dark; --accent: #2a78d6; --track: #e1e0d9; }
body { font: 15px/1.5 system-ui, -apple-system, Segoe UI, sans-serif;
       margin: 0; padding: 24px; background: #fbfbfd; color: #16181d; }
@media (prefers-color-scheme: dark) {
  :root { --accent: #3987e5; --track: #2c2c2a; }
  body { background: #14161a; color: #e8eaed; }
  th { background: #1e2127 !important; }
  tr:nth-child(even) td { background: #191c21; }
  .card { background: #1a1d22 !important; border-color: #2a2e35 !important; }
}
h1 { font-size: 22px; margin: 0 0 4px; }
.sub { opacity: .7; font-size: 15px; margin-bottom: 20px; }
.card { background: #fff; border: 1px solid #e3e5ea; border-radius: 10px;
        padding: 14px 16px; margin-bottom: 18px; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
.scroll { overflow-x: auto; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid #e6e8ec;
         vertical-align: top; }
th { background: #f2f3f6; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
ul.streams { margin: 0; padding-left: 18px; }
.empty { opacity: .7; font-style: italic; }
.note { font-size: 14px; opacity: .7; margin-top: 20px; }
"""


def _esc(value):
    return html.escape(str(value if value is not None else ""), quote=True)


def _fmt_ts(ts):
    """Render the generation time in UTC, labelled as such.

    Deliberately not local time: this module has no access to Dispatcharr's
    configured timezone, and a bare unlabelled clock that silently means UTC is
    how a reader gets the day wrong.
    """
    try:
        moment = datetime.datetime.fromtimestamp(float(ts or 0), datetime.timezone.utc)
        return moment.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "unknown"


def render_html(model):
    """Render the model to one self-contained HTML page."""
    rows = []
    for entry in model.get("entries") or []:
        names = entry.get("stream_names") or []
        if names:
            streams = ("<ul class=\"streams\">"
                       + "".join(f"<li>{_esc(n)}</li>" for n in names)
                       + "</ul>")
        else:
            streams = "<span class=\"empty\">no streams matched</span>"
        rows.append(
            "<tr>"
            f"<td>{_esc(entry.get('channel_name'))}</td>"
            f"<td class=\"num\">{_esc(entry.get('matched'))}</td>"
            f"<td>{streams}</td>"
            "</tr>"
        )
    if rows:
        table = ("<div class=\"scroll\"><table>"
                 "<tr><th>Channel</th><th>Matched</th><th>Streams</th></tr>"
                 + "".join(rows) + "</table></div>")
    else:
        table = "<p class=\"empty\">No channels were matched in this run.</p>"

    count = _esc(model.get("channel_count", 0))
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Stream-Mapparr report</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        "<h1>Stream-Mapparr report</h1>\n"
        f"<div class=\"sub\">{count} channel(s) &middot; generated {_esc(_fmt_ts(model.get('generated_ts')))}</div>\n"
        f"<div class=\"card\">{table}</div>\n"
        "<p class=\"note\">Stream names in this report are shown without their M3U "
        "source label. The CSV exports in /data/exports include that label and are "
        "not sent by email.</p>\n"
        "</body>\n</html>\n"
    )


# A cell beginning with any of these is evaluated as a formula by Excel,
# LibreOffice and Google Sheets when the file is opened.
_FORMULA_LEADS = ("=", "+", "-", "@")


def _csv_safe(value):
    """Prefix a formula-shaped cell with an apostrophe so it stays text."""
    text = str(value if value is not None else "")
    if text[:1] in _FORMULA_LEADS:
        return "'" + text
    return text


def render_csv(model):
    """Render the model to CSV text, with the same content rules as the HTML."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["channel_name", "matched", "stream_names"])
    for entry in model.get("entries") or []:
        writer.writerow([
            _csv_safe(entry.get("channel_name")),
            entry.get("matched", 0),
            _csv_safe("; ".join(entry.get("stream_names") or [])),
        ])
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #

def _atomic_write(path, text):
    """Write through a temporary file and rename, so no partial file is ever
    visible at the destination path."""
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _prune(dirpath, suffix, keep=KEEP_REPORTS):
    """Keep the newest `keep` files with this suffix, delete the rest.

    Never raises: losing an old report must not fail the run that produced a
    new one.
    """
    try:
        entries = [os.path.join(dirpath, n) for n in os.listdir(dirpath)
                   if n.startswith("stream_mapparr_report_") and n.endswith(suffix)]
        entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for stale in entries[keep:]:
            try:
                os.unlink(stale)
            except Exception:
                pass
    except Exception:
        pass


def write_report(model, report_dir, now):
    """Write the HTML and CSV reports and return their paths.

    Returns {"html_path", "csv_path", "error"}. Never raises: reporting is not
    the plugin's real work, and a failure here is reported rather than thrown.

    Both files carry the run's timestamp in their name and are never rewritten,
    because an SMTP send re-reads the attachment path on every retry attempt.
    """
    result = {"html_path": None, "csv_path": None, "error": None}
    try:
        os.makedirs(report_dir, exist_ok=True)
        stamp = datetime.datetime.fromtimestamp(
            float(now or 0), datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
        base = os.path.join(report_dir, f"stream_mapparr_report_{stamp}")
        html_path, csv_path = base + ".html", base + ".csv"
        _atomic_write(html_path, render_html(model))
        _atomic_write(csv_path, render_csv(model))
        result["html_path"], result["csv_path"] = html_path, csv_path
        _prune(report_dir, ".html")
        _prune(report_dir, ".csv")
    except Exception as e:
        result["error"] = f"could not write the report: {e}"
    return result
