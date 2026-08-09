"""Shared page furniture for the HTML reports these plugins email.

WHY THIS EXISTS. Two plugins here render a report page and both had their own
copy of the same stylesheet, masthead, stat tiles, bar chart and footer. The
copies had already drifted. This module holds one definition so they cannot.

WHAT IT IS NOT. It holds no knowledge of any plugin's data. A caller builds its
own rows and sections and passes rendered fragments in. The module owns colour,
spacing, the document shell and the handful of primitives that carry them.

VENDORING. A plugin cannot import from this directory at run time: it deploys as
a self-contained folder into /data/plugins, where this path does not exist. So
this file is copied into each plugin and pinned by hash, exactly as
notify_client.py and matching_core.py are. Do not hand-edit a vendored copy.

THE PALETTE IS THE EXTENSION POINT. A caller passes an ordered mapping of accent
name to a light and dark colour. Each entry produces a --NAME custom property, a
.dot-NAME class and a .bar-NAME class. Callers therefore choose their own
vocabulary and no plugin's categories are baked in here.

COLOUR IS APPLIED BY CLASS, NEVER BY A FILL ATTRIBUTE HOLDING A CUSTOM PROPERTY.
fill="var(--x)" as an SVG presentation attribute has patchy support and falls
back to BLACK when unsupported, which is an invisible chart on a dark surface.

THREE THEME STATES, NOT TWO. An explicit choice stamps data-theme on the root
element. The default setting stamps nothing, so only prefers-color-scheme
separates light from dark. Every colour is therefore defined on bare :root
first, then redefined under the media query, then again under the attribute, so
neither the system setting nor an explicit override can leave a token unset.
"""

import base64
import html
import os

__version__ = "1.0.0"

# A logo rides on every emailed copy of the report, so it is capped rather than
# trusted. Over the cap the page renders with no image at all.
LOGO_MAX_ENCODED_BYTES = 96 * 1024

# The neutral tokens are identical for every caller. Only the accents differ.
_NEUTRAL_LIGHT = [
    ("bg", "#fbfbfd"), ("surface", "#ffffff"), ("border", "#e3e5ea"),
    ("zebra", "#f7f8fa"), ("head", "#f2f3f6"), ("line-soft", "#e6e8ec"),
    ("track", "#e1e0d9"),
    ("ink", "#16181d"), ("ink-muted", "#5c616b"), ("ink-dim", "#656a76"),
]
_NEUTRAL_DARK = [
    ("bg", "#14161a"), ("surface", "#1a1d22"), ("border", "#2a2e35"),
    ("zebra", "#191c21"), ("head", "#1e2127"), ("line-soft", "#262a31"),
    ("track", "#2c2c2a"),
    ("ink", "#e8eaed"), ("ink-muted", "#a7adb8"), ("ink-dim", "#9aa0ab"),
]
_LIFT_LIGHT = "0 1px 2px rgba(16, 18, 29, .05), 0 4px 12px rgba(16, 18, 29, .04)"
_LIFT_DARK = "0 1px 2px rgba(0, 0, 0, .35), 0 4px 12px rgba(0, 0, 0, .25)"
_FOCUS_LIGHT = "#2a78d6"
_FOCUS_DARK = "#3987e5"


def _token_lines(neutral, accents, lift, focus, index, indent="  "):
    """The custom-property body shared by all three theme blocks.

    `index` selects light (0) or dark (1) from each accent's colour pair.
    """
    out = []
    out.append(indent + "; ".join("--%s: %s" % (k, v) for k, v in neutral[:3]) + ";")
    out.append(indent + "; ".join("--%s: %s" % (k, v) for k, v in neutral[3:7]) + ";")
    out.append(indent + "; ".join("--%s: %s" % (k, v) for k, v in neutral[7:]) + ";")
    out.append(indent + "--lift: %s;" % lift)
    names = list(accents)
    for start, stop in ((0, 3), (3, len(names))):
        chunk = names[start:stop]
        if chunk:
            out.append(indent + "; ".join(
                "--%s: %s" % (n, accents[n][index]) for n in chunk) + ";")
    out.append(indent + "--focus: %s;" % focus)
    return out


def build_css(accents, extra=""):
    """The complete stylesheet for a report page.

    `accents` is an ordered mapping of name -> (light_colour, dark_colour).
    `extra` is appended verbatim for rules only one caller needs.
    """
    accents = dict(accents or {})
    light = _token_lines(_NEUTRAL_LIGHT, accents, _LIFT_LIGHT, _FOCUS_LIGHT, 0)
    dark = _token_lines(_NEUTRAL_DARK, accents, _LIFT_DARK, _FOCUS_DARK, 1)
    dark_nested = ["  " + line for line in dark]

    out = ["\n:root {", "  color-scheme: light dark;",
           "  --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px;"]
    out.extend(light)
    out.append("}")
    out.append("@media (prefers-color-scheme: dark) {")
    out.append("  :root {")
    out.extend(dark_nested)
    out.append("  }")
    out.append("}")
    out.append(':root[data-theme="dark"] {')
    out.extend(dark)
    out.append("}")
    out.append(':root[data-theme="light"] {')
    out.extend(light)
    out.append("}")
    out.append(_STRUCTURE)
    for name in accents:
        out.append(".dot-%s { background: var(--%s); }" % (name, name))
    out.append(_MIDDLE)
    for name in accents:
        out.append(".bar-%s { fill: var(--%s); }" % (name, name))
    out.append(_TAIL)
    if extra:
        out.append(extra.strip("\n"))
    return "\n".join(out) + "\n"


_STRUCTURE = """body {
  margin: 0; padding: var(--s5);
  background: var(--bg); color: var(--ink);
  font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
/* The logo sits beside the title rather than above it, so the masthead costs
   one line of vertical space instead of three. */
.masthead { display: flex; align-items: center; gap: var(--s3); margin-bottom: var(--s5); }
.mark { flex: none; width: 48px; height: 48px; display: block; }
h1 { font-size: 22px; line-height: 1.2; letter-spacing: -.01em; margin: 0 0 var(--s1) 0; }
.meta { color: var(--ink-muted); font-size: 15px; }
.totals { display: flex; flex-wrap: wrap; gap: var(--s3); margin-bottom: var(--s5); }
.tile {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 10px; box-shadow: var(--lift);
  padding: var(--s3) var(--s4); min-width: 128px;
}
.tile .n { font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; }
.tile .k { color: var(--ink-muted); font-size: 15px; }
.chart { margin-bottom: var(--s5); }
/* Flat disclosure rows separated by a rule, not stacked cards: at this row
   count a card per section reads as a wall of boxes. */
details { border-top: 1px solid var(--track); padding: var(--s1) 0 var(--s2); }
summary {
  cursor: pointer; font-size: 17px; font-weight: 600;
  padding: var(--s2) var(--s1); list-style: none;
}
summary::-webkit-details-marker { display: none; }
summary::before {
  content: '\\25B8'; display: inline-block; width: 1em;
  color: var(--ink-dim); transition: transform .12s;
}
details[open] > summary::before { transform: rotate(90deg); }
summary:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; }
.dot {
  display: inline-block; width: 10px; height: 10px; border-radius: 50%;
  margin-right: var(--s2); vertical-align: baseline;
}"""

_MIDDLE = """.glyph { margin-right: var(--s2); }
/* The heading is 600; the count staying at 400 is what separates the two, so
   the number reads as data rather than as part of the label. */
.count { font-weight: 400; color: var(--ink-dim); margin-left: var(--s2);
         font-variant-numeric: tabular-nums; }
.sub { color: var(--ink-muted); font-size: 15px; margin: var(--s2) 0 var(--s1) 0; }
.act { color: var(--ink); font-size: 15px; margin: 0 0 var(--s2) 0; font-weight: 600; }
.hint { color: var(--ink-dim); font-size: 15px; margin: 0 0 var(--s3) 0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 15px; }
th, td { text-align: left; padding: var(--s2) var(--s3); border-bottom: 1px solid var(--line-soft); }
th { background: var(--head); color: var(--ink-muted); font-weight: 600;
     position: sticky; top: 0; }
tr:nth-child(even) td { background: var(--zebra); }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.empty { color: var(--ink-dim); font-size: 15px; margin: var(--s2) 0; }
.bar-label { fill: var(--ink-muted); font-size: 13px; }"""

_TAIL = """.colophon {
  margin-top: var(--s5); padding-top: var(--s4);
  border-top: 1px solid var(--track);
  color: var(--ink-dim); font-size: 15px;
}
.colophon p { margin: 0 0 var(--s1) 0; }
.colophon a { color: var(--focus); }"""


# --------------------------------------------------------------------------- #
# Primitives. Every one is total over its input: a report must never fail to
# render because a value was missing or the wrong type.
# --------------------------------------------------------------------------- #

def esc(value):
    """HTML-escape any value. None and non-strings become text first."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def logo_data_uri(plugin_dir, filename="logo.png", max_encoded=LOGO_MAX_ENCODED_BYTES):
    """Base64 data URI for a plugin logo, or None.

    None means the masthead renders with NO image element at all, rather than a
    broken-image icon. A logo that cannot be read, or that is too large to ride
    on every emailed copy, must never fail a report.
    """
    try:
        path = os.path.join(plugin_dir or "", filename)
        with open(path, "rb") as handle:
            raw = handle.read()
    except (OSError, TypeError, ValueError):
        return None
    try:
        encoded = base64.b64encode(raw).decode("ascii")
    except Exception:
        return None
    if len(encoded) > max_encoded:
        return None
    return "data:image/png;base64," + encoded


def masthead(title, meta, logo=None):
    """The page header: optional logo, title, one line of metadata.

    `meta` is inserted as already-escaped markup so a caller can include a
    separator or a link. Escape anything user-supplied before passing it.
    """
    out = ['<header class="masthead">']
    if logo:
        out.append('<img class="mark" src="%s" alt="" width="48" height="48">' % logo)
    out.extend(["<div><h1>", esc(title), "</h1>",
                '<div class="meta">', meta or "", "</div></div></header>"])
    return "".join(out)


def tile(number, label):
    """One statistic. The number is the fact; the label says what it counts."""
    return ('<div class="tile"><div class="n">%s</div><div class="k">%s</div></div>'
            % (esc(number), esc(label)))


def tiles(pairs):
    """A row of statistics from an iterable of (number, label)."""
    body = "".join(tile(n, k) for n, k in (pairs or []))
    return '<div class="totals">%s</div>' % body


def bar_chart(items, aria_label="Counts by group"):
    """Inline SVG bar chart from an iterable of (title, count, accent_name).

    Returns an empty string when there is nothing to plot or every count is
    zero, so a caller can append the result unconditionally. Total over its
    input: an all-zero set must not divide by zero.
    """
    rows = []
    for entry in items or []:
        try:
            title, count, accent = entry
        except (TypeError, ValueError):
            continue
        try:
            count = int(count)
        except (TypeError, ValueError):
            continue
        rows.append((title or "", count, "bar-%s" % accent))
    if not rows:
        return ""
    widest = max(n for _, n, _ in rows)
    if widest <= 0:
        return ""
    row_h, gap, label_w, bar_max = 26, 6, 250, 380
    height = len(rows) * (row_h + gap)
    width = label_w + bar_max + 70
    out = ['<svg class="chart" role="img" aria-label="%s" ' % esc(aria_label),
           'viewBox="0 0 %d %d" width="100%%" height="%d">' % (width, height, height)]
    for index, (title, count, cls) in enumerate(rows):
        y = index * (row_h + gap)
        bar_w = int(bar_max * count / widest)
        out.append('<text class="bar-label" x="0" y="%d">%s</text>' % (y + 18, esc(title)))
        out.append('<rect class="%s" x="%d" y="%d" width="%d" height="%d" rx="3"></rect>'
                   % (cls, label_w, y, bar_w, row_h))
        out.append('<text class="bar-label" x="%d" y="%d">%d</text>'
                   % (label_w + bar_w + 8, y + 18, count))
    out.append("</svg>")
    return "".join(out)


FIND_HINT = ("Expand this section before using your browser find on this page. "
             "Text inside a collapsed section is not searchable in some browsers.")


def section(title, count, accent, body, description="", action="", glyph="",
            find_hint=True):
    """One collapsible section.

    The details element needs no JavaScript, and a client that does not
    implement it renders the content EXPANDED, so the failure mode is
    everything visible rather than content lost. Sections start COLLAPSED.

    `count` is stated by the caller rather than derived, because the number in
    the heading must describe the rows beneath it and only the caller knows
    whether its body is a table, a list, or something else. `body` is inserted
    as markup and is the caller's responsibility to escape.
    """
    out = ["<details><summary>",
           '<span class="dot dot-%s" aria-hidden="true"></span>' % accent]
    if glyph:
        out.extend(['<span class="glyph" aria-hidden="true">', glyph, "</span>"])
    out.extend([esc(title), '<span class="count">', esc(count), "</span>", "</summary>"])
    if description:
        out.extend(['<p class="sub">', esc(description), "</p>"])
    if action:
        out.extend(['<p class="act">What to do: ', esc(action), "</p>"])
    if find_hint:
        out.extend(['<p class="hint">', esc(FIND_HINT), "</p>"])
    out.append(body or '<p class="empty">Nothing in this group.</p>')
    out.append("</details>")
    return "".join(out)


def table(headers, rows):
    """A scrollable table. `rows` holds already-rendered <tr> markup."""
    rows = list(rows or [])
    if not rows:
        return ""
    head = "".join("<th>%s</th>" % esc(h) for h in headers or [])
    return ('<div class="scroll"><table><thead><tr>%s</tr></thead><tbody>%s'
            "</tbody></table></div>" % (head, "".join(rows)))


def colophon(paragraphs, links=None):
    """The footer. `links` is an iterable of (text, href), joined by a middot.

    A period is used rather than an em dash: these reports are read in mail
    clients whose encoding handling is not guaranteed, and the plugins here
    forbid em dashes in text a user sees.
    """
    out = ['<footer class="colophon">']
    for text in paragraphs or []:
        out.extend(["<p>", text, "</p>"])
    pairs = list(links or [])
    if pairs:
        rendered = ' . '.join('<a href="%s">%s</a>' % (esc(href), esc(text))
                              for text, href in pairs)
        out.extend(["<p>", rendered, "</p>"])
    out.append("</footer>")
    return "".join(out)


def page(title, css, body_parts, lang="en"):
    """A complete, self-contained document.

    THE CHARSET LINE IS NOT OPTIONAL. Report headings carry emoji, the file is
    written as UTF-8, and without a declared encoding a browser opening it from
    disk and a mail client rendering it as an attachment both fall back to a
    legacy single-byte encoding and show each emoji as a run of wrong
    characters. Observed in a real emailed copy.

    Assembled by joining a list rather than by one large formatted string, so a
    literal brace in the stylesheet cannot become a format field.
    """
    out = ["<!doctype html>", '<html lang="%s">' % esc(lang), "<head>",
           '<meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           "<title>", esc(title), "</title>", "<style>", css, "</style>",
           "</head>", "<body>"]
    out.extend(part for part in (body_parts or []) if part)
    out.extend(["</body>", "</html>"])
    return "".join(out)
