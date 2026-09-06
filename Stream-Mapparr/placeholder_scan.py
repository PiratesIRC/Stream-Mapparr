"""Find numbered stream-name families that no placeholder pattern covers.

GitHub issue #43. The setting `epg_placeholder_name_patterns` only ever helps
with the naming schemes the operator already thought to write down, and nothing
in the interface separates "this installation has no placeholder families" from
"the patterns you wrote match none of them". The reporter discovered two whole
uncovered families, one of them their largest, only by pulling every stream
name through the API by hand and grouping it.

This module is the grouping, as a pure stdlib unit with no Django and no
plugin import, so it can be tested directly. The plugin action is the thin
wrapper that loads the streams and writes the readout.

TWO THINGS ARE BUILT IN RATHER THAN LEFT TO BE DISCOVERED LATER, both measured
on a live installation of 25,323 stream names before any of this was written:

  A digit immediately followed by K is a resolution tag, not a slot number.
  Replacing it turns `4K` and `8K` into `#K`, which merged 293 unrelated names
  into a single false family driven entirely by the resolution tag.

  Not every numbered family is a placeholder. `UK: BBC RED BUTTON #`,
  `US: HULU ORIGINALS #` and `UK: KARAOKE #` are numbered channel families
  whose names are perfectly informative, and adding a placeholder pattern for
  them would make matching worse. A placeholder can only ever be RESOLVED if
  its streams carry EPG data, so families are ranked by how many of their
  members carry an EPG identifier and the merely-numbered ones sink.

This reports. It never edits the setting, and nothing here writes to the
database.
"""

# A family needs at least this many DIFFERENT numbers in its slot before it is
# worth reporting. Counting streams instead would let one stream name carried by
# several M3U accounts look like a numbered family: five rows of `HBO 1` are
# five sources for one name, not five slots. A count threshold as well was
# tried and removed, because a family with three distinct numbers already has
# at least three streams, so the second rule could never refuse anything the
# first one admitted and no test could tell whether it was doing any work.
MIN_DISTINCT_NUMBERS = 3

# How many uncovered families are described in full, with a pattern to paste.
# MEASURED on 25,068 live stream names: 131 families are uncovered. Describing
# every one produces the long, mostly unactionable readout this feature exists
# to replace, so the rest are listed as one line each and the count of what was
# left out is stated rather than the cut being silent.
DETAIL_LIMIT = 25

# The slot marker in a template. A name containing a literal one is stored
# with it backslash-escaped so the two can never be confused.
SLOT = "#"

# Characters that need escaping to appear literally in the suggested regex.
# Deliberately NOT re.escape: since Python 3.7 that also escapes the space and
# the hash, which would turn a readable, pasteable suggestion such as
# ^Triller TV \| Event \d+$ into an unreadable one.
_REGEX_SPECIALS = set(r"()[]{}?*+-|^$\.")


def ascii_safe(text):
    """Rewrite every non-ASCII character as a backslash-u escape.

    The readout is plain ASCII on purpose, the same rule as the CSV export
    preamble: it is opened in a text editor that may be using another codepage,
    where a non-ASCII byte becomes mojibake. Provider stream names really do
    carry such characters. MEASURED on this installation: family templates
    include a circled bullet and superscript letters, so writing names through
    unescaped would have broken the rule on live data while every synthetic
    test still passed.

    The escape is not merely readable, it is also correct inside a suggested
    pattern: Python's regular expressions accept backslash-u, so a pattern written
    this way still matches the real name.
    """
    return "".join(ch if ord(ch) < 128 else "\\u%04x" % ord(ch) for ch in text)


def _escape_literal(text):
    """Escape `text` so it matches itself inside a regular expression."""
    return "".join("\\" + ch if ch in _REGEX_SPECIALS else ch for ch in text)


def template_of(name):
    """Split `name` into its digit-stripped template and the numbers removed.

    Returns `(template, numbers)`. `numbers` is a tuple of the digit runs in
    the order they appeared, so a caller can count distinct slot values, and it
    is empty when the name holds no slot at all.

    A digit run is left alone, and so contributes no slot, when it starts at a
    word boundary and is immediately followed by the letter K. That is a
    resolution tag (`4K`, `8k`), not a numbered slot. The boundary is required:
    in `X264K` the run is glued to a letter on the left, so it is a slot and
    the template is `X#K`.
    """
    if not name:
        return ("", ())
    parts = []
    numbers = []
    index = 0
    length = len(name)
    while index < length:
        char = name[index]
        if not char.isdigit():
            parts.append("\\" + SLOT if char == SLOT else char)
            index += 1
            continue
        start = index
        while index < length and name[index].isdigit():
            index += 1
        run = name[start:index]
        at_boundary = start == 0 or not name[start - 1].isalnum()
        followed_by_k = index < length and name[index] in ("k", "K")
        if at_boundary and followed_by_k:
            parts.append(run)
            continue
        parts.append(SLOT)
        numbers.append(run)
    return ("".join(parts), tuple(numbers))


def suggested_pattern(template):
    """Turn a template into an anchored regex the operator can paste.

    Every slot becomes `\\d+`, an escaped literal hash becomes a literal hash,
    and everything else is escaped so it matches itself. Anchored at both ends
    because the plugin decides placeholder eligibility with `fullmatch` over
    the whole name.
    """
    out = []
    literal = []
    index = 0
    length = len(template)
    while index < length:
        char = template[index]
        if char == "\\" and index + 1 < length and template[index + 1] == SLOT:
            literal.append(SLOT)
            index += 2
            continue
        if char == SLOT:
            out.append(_escape_literal("".join(literal)))
            literal = []
            out.append(r"\d+")
            index += 1
            continue
        literal.append(char)
        index += 1
    out.append(_escape_literal("".join(literal)))
    return ascii_safe("^" + "".join(out) + "$")


def _has_epg_identifier(stream):
    return bool((stream.get("tvg_id") or "").strip())


def scan_families(streams, patterns):
    """Group `streams` by template and describe every numbered family found.

    `patterns` is the list of compiled placeholder patterns already configured.
    Coverage is decided with `fullmatch`, the same way the matcher itself
    decides whether a name is a placeholder, so an unanchored pattern that
    would merely search-match does not count as covering anything.

    Returns a list of dicts, best candidate first, ranked by how many members
    carry an EPG identifier and then by size. A family is reported whether or
    not it is covered; the caller decides what to show.
    """
    grouped = {}
    for stream in streams:
        name = stream.get("name") or ""
        if not name:
            continue
        template, numbers = template_of(name)
        if not numbers:
            continue
        family = grouped.setdefault(template, {
            "template": template,
            "count": 0,
            "numbers": set(),
            "example": name,
            "with_epg_id": 0,
            "covered": 0,
        })
        family["count"] += 1
        family["numbers"].add(numbers)
        if name < family["example"]:
            family["example"] = name
        if _has_epg_identifier(stream):
            family["with_epg_id"] += 1
        if patterns and any(p.fullmatch(name) for p in patterns):
            family["covered"] += 1

    families = []
    for family in grouped.values():
        distinct = len(family["numbers"])
        if distinct < MIN_DISTINCT_NUMBERS:
            continue
        families.append({
            "template": family["template"],
            "count": family["count"],
            "distinct_numbers": distinct,
            "example": family["example"],
            "with_epg_id": family["with_epg_id"],
            "covered": family["covered"],
            "uncovered": family["count"] - family["covered"],
            "suggested": suggested_pattern(family["template"]),
        })
    families.sort(key=lambda f: (-f["with_epg_id"], -f["count"], f["template"]))
    return families


def render_report(families, total_streams, pattern_count, feature_enabled):
    """The operator-facing readout, plain ASCII.

    ASCII on purpose, the same rule as the CSV export preamble: this file is
    opened in a text editor that may be using another codepage, and a non-ASCII
    character there becomes mojibake.
    """
    uncovered = [f for f in families if f["uncovered"] > 0]
    covered = [f for f in families if f["uncovered"] == 0]

    lines = [
        "Stream-Mapparr: placeholder name family scan",
        "============================================",
        "",
        "Every stream name is grouped by replacing its numbers with a slot, so",
        "MAX 100 and MAX 101 become the one family MAX #. A family is reported",
        f"when at least {MIN_DISTINCT_NUMBERS} different numbers appear in its slot,",
        "which also means at least that many streams. Counting streams alone",
        "would read one name carried by several sources as a family.",
        "",
        "A character outside plain ASCII is written as a backslash-u escape. Python",
        "regular expressions accept that form, so a suggested pattern still",
        "matches the real name.",
        "",
        "A digit followed by K is left alone, because 4K and 8K are resolution",
        "tags rather than slot numbers.",
        "",
        "This reports only. No setting is changed and nothing is written to the",
        "database. Read a suggestion before pasting it: not every numbered",
        "family is a placeholder. A numbered channel family whose names are",
        "already informative is matched better as it is.",
        "",
        f"Streams scanned                 : {total_streams}",
        f"Numbered families found         : {len(families)}",
        f"Placeholder patterns configured : {pattern_count}",
        f"Families no pattern covers      : {len(uncovered)}",
        "",
    ]

    if not feature_enabled:
        lines += [
            "NOTE: EPG-Based Placeholder Matching is OFF, so the pattern list is",
            "never consulted and no family is covered in practice, whatever is",
            "written in it. Turn the feature on for these patterns to do anything.",
            "",
        ]

    actionable = [f for f in uncovered if f["with_epg_id"] > 0]
    no_epg = [f for f in uncovered if f["with_epg_id"] == 0]

    if uncovered:
        word = "family" if len(actionable) == 1 else "families"
        lines.append(f"{len(actionable)} likely placeholder {word} not covered by your "
                     f"current patterns")
        lines.append("whose streams carry EPG data, best candidate first. The ranking is")
        lines.append("how many streams in the family carry an EPG identifier, because a")
        lines.append("placeholder can only ever be resolved when its streams carry EPG")
        lines.append("data to resolve it from.")
        lines.append("")
        if not actionable:
            lines.append("  None. Every uncovered family is listed below instead.")
            lines.append("")
        for family in actionable[:DETAIL_LIMIT]:
            lines.append("  " + ascii_safe(family["template"]))
            lines.append(f"      streams                : {family['count']}"
                         f" ({family['uncovered']} not covered)")
            lines.append(f"      different slot numbers : {family['distinct_numbers']}")
            lines.append(f"      carrying an EPG id     : {family['with_epg_id']}")
            lines.append("      example                : " + ascii_safe(family["example"]))
            lines.append(f"      pattern to paste       : {family['suggested']}")
            lines.append("")
        if len(actionable) > DETAIL_LIMIT:
            lines.append(f"  and {len(actionable) - DETAIL_LIMIT} more, not described here.")
            lines.append("")

    if no_epg:
        lines.append(f"{len(no_epg)} further uncovered families carry no EPG data at all.")
        lines.append("A placeholder pattern for one of these could not resolve anything")
        lines.append("today, so no pattern is suggested. They are listed because a family")
        lines.append("can start carrying EPG data later, and because a numbered channel")
        lines.append("family whose names are already informative belongs here rather than")
        lines.append("in the list above.")
        lines.append("")
        for family in no_epg[:DETAIL_LIMIT * 4]:
            lines.append("  " + ascii_safe(family["template"]) + f"   ({family['count']} streams)")
        if len(no_epg) > DETAIL_LIMIT * 4:
            lines.append(f"  and {len(no_epg) - DETAIL_LIMIT * 4} more, not listed here.")
        lines.append("")

    if not uncovered:
        lines.append("No uncovered numbered families were found.")
        lines.append("")

    if covered:
        lines.append("Families your patterns already cover in full:")
        for family in covered:
            lines.append("  " + ascii_safe(family["template"]) + f"   ({family['count']} streams)")
        lines.append("")

    return "\n".join(lines)
