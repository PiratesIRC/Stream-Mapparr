"""Report model and rendering for Stream-Mapparr.

The model is built by COPYING a known set of keys rather than by filtering an
unknown one. Newsflasharr sends an attachment verbatim and unredacted, so
anything reaching this model can leave the box by email, and a column added to
an export later must not be able to start being emailed on its own.

Two measured facts about this installation drive the design:

1. Every CSV export in /data/exports appends the M3U account name to each stream
   name, and an account name is commonly the provider's hostname.
2. Bracketed text in a stream name is usually the market rather than a source
   label, and for an over-the-air station the market is its whole identity.
   Measured on one real installation: 327 bracketed names, none an account name.

So the primary defence is that build_model is fed RAW stream names, which never
carried an account label. sanitise_stream_label is the backstop: it removes an
account name wherever it appears, case-insensitively, bracketed or bare, and
leaves every other bracketed value alone because those hold the market.
"""
import csv
import datetime
import html
import io
import os
import re
import time

# Reports are written here, deliberately NOT under /data/logos: Dispatcharr's
# nginx serves that tree unauthenticated to the entire local network.
REPORT_DIR = "/data/stream_mapparr_reports"

# How many of each file type to keep. Newsflasharr re-reads an attachment path
# on every retry attempt across a worst case of 2130 seconds, about 35 minutes,
# so retention must exceed that by a wide margin. At one report per run, eight
# is days of headroom.
KEEP_REPORTS = 8

# Newsflasharr re-reads an attachment path on every delivery retry, across a
# documented worst case of 30 + 300 + 1800 seconds. A report file younger than
# this is never pruned, however many newer ones exist, because deleting it
# would strip the attachment from mail already queued.
RETRY_WINDOW_SECONDS = 2400

# Matches a bare IPv4 address. The Sort export carries provider edge server
# addresses in a column of its own; this catches one reaching a name field by
# any other route.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")

# Matches an IPv6 address, including the compressed "::" forms. Added after a
# security review of the written code: the IPv4 pattern above does not match
# one, so an edge server address given in that form shipped unredacted.
# Deliberately requires either a "::" or at least three colon-separated groups,
# so an ordinary time like "20:30" is not mistaken for an address.
_IPV6_RE = re.compile(
    r"\b(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{1,4}\b"
    r"|(?:[0-9A-Fa-f]{1,4}:)+:(?:[0-9A-Fa-f]{1,4})?"
    r"|::(?:[0-9A-Fa-f]{1,4}:)*[0-9A-Fa-f]{1,4}"
)

# Collapses the run of spaces left behind when an address is removed from the
# middle of a name.
_MULTISPACE_RE = re.compile(r"\s{2,}")


def sanitise_stream_label(label, account_names):
    """Remove an M3U account name wherever it appears, and nothing else.

    An unknown bracketed value is left alone, because on this installation those
    hold the market rather than a source label. Removing every bracketed group
    would strip nothing that leaks and would collapse dozens of distinct
    over-the-air stations into one indistinguishable name.

    Matching is case-insensitive and is NOT limited to the bracketed form. The
    account names on a real installation are literal provider hostnames, so
    "ESPN backup provider.tv" leaks exactly as much as "ESPN [provider.tv]", and
    "[PROVIDER.TV]" leaks the same hostname in different case. An earlier version
    matched only the exact-case bracketed form, which a security review of the
    written code found to be a narrower guarantee than the docstring claimed.

    Account names are matched longest first: "provider.tv" is a prefix of
    "provider.tv-alt1", and matching the shorter one first would leave a
    "-alt1" fragment behind.
    """
    text = str(label or "")
    for account in sorted([a for a in (account_names or []) if a], key=len, reverse=True):
        escaped = re.escape(account)
        # The bracketed form first, so the brackets go with it rather than
        # being left behind as an empty pair.
        text = re.sub(r"\s*\[" + escaped + r"\]", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*\(" + escaped + r"\)", "", text, flags=re.IGNORECASE)
        text = re.sub(escaped, "", text, flags=re.IGNORECASE)
    return _MULTISPACE_RE.sub(" ", text).strip()


def _scrub(value, account_names):
    """Apply every content rule to one free-text value."""
    cleaned = sanitise_stream_label(value, account_names)
    cleaned = _IPV4_RE.sub("", cleaned)
    cleaned = _IPV6_RE.sub("", cleaned)
    return _MULTISPACE_RE.sub(" ", cleaned).strip()


def _collapse_repeats(names):
    """Collapse repeated names to one line each, annotated with a count.

    The same stream name legitimately exists in several M3U accounts, and all
    of them get matched, so a channel really does receive three streams called
    the same thing from three different sources. The source label is what told
    them apart, and removing it is deliberate because that label is a provider
    hostname. Without this, the first real report showed the same entry three
    times with nothing to distinguish them.

    A count keeps the information the label carried, which is HOW MANY sources,
    without naming them. First-appearance order is preserved.
    """
    counts = {}
    order = []
    for name in names:
        if name not in counts:
            counts[name] = 0
            order.append(name)
        counts[name] += 1
    return [f"{name} (x{counts[name]})" if counts[name] > 1 else name
            for name in order]


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
            # The count is what was actually assigned, so it counts every
            # stream, before the display below collapses repeated names.
            "matched": int(row.get("matched") or len(names)),
            "stream_names": _collapse_repeats(names),
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


def _prune(dirpath, suffix, keep=KEEP_REPORTS, now=None):
    """Keep the newest `keep` files with this suffix, delete the rest, EXCEPT
    any file still young enough that a delivery retry could need it.

    The age guard is not belt-and-braces, it is required. Newsflasharr copies
    nothing: it re-reads the attachment path on every retry attempt across a
    worst case of 2130 seconds. A count-only rule is not enough on this plugin
    because the event-driven auto-match fires once per M3U account when an M3U
    source refreshes, so a burst can produce several report pairs within
    minutes and push earlier ones past the keep count while their mail is still
    being retried. The result would be an email arriving with its attachment
    missing, which Newsflasharr records as a degrade rather than an error.

    Never raises: losing an old report must not fail the run that produced a
    new one.
    """
    try:
        moment = time.time() if now is None else now
        entries = [os.path.join(dirpath, n) for n in os.listdir(dirpath)
                   if n.startswith("stream_mapparr_report_") and n.endswith(suffix)]
        entries.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for stale in entries[keep:]:
            try:
                if moment - os.path.getmtime(stale) < RETRY_WINDOW_SECONDS:
                    continue  # a queued delivery may still re-read this path
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
