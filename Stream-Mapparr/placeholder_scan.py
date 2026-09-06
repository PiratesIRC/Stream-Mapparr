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
import time

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

# The Placeholder Name Patterns setting refuses a pattern longer than this, so a
# suggestion over the limit would be skipped in silence if it were pasted. The
# readout says so rather than offering it as though it worked. The number is
# PluginConfig.REGEX_PATTERN_MAX_LEN, repeated here because this module has no
# plugin import; tests/test_placeholder_scan.py pins the two together.
SUGGESTION_MAX_LEN = 500

# How often the walk hands control back to the caller. The plugin runs under
# gevent, where a loop that never yields freezes the entire worker.
YIELD_EVERY = 500

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
    out = []
    for ch in text:
        code = ord(ch)
        if code < 128:
            out.append(ch)
        elif code <= 0xFFFF:
            out.append("\\u%04x" % code)
        else:
            # The lower-case form has a MINIMUM width of four, not a fixed
            # one, so an emoji produced five hex digits and Python read only
            # the first four. The pasted pattern then matched nothing and the
            # family kept reporting as uncovered with nothing saying why.
            out.append("\\U%08x" % code)
    return "".join(out)


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
            if char in (SLOT, "\\"):
                # Both are escaped, and the backslash MUST be, or a backslash
                # followed by a digit produces the same two characters as an
                # escaped hash: two different names then group into one family
                # and neither is matched by the pattern the family suggests.
                parts.append("\\" + char)
            else:
                parts.append(char)
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
            # The K is upper-cased in the TEMPLATE only, never in the name.
            # Patterns are compiled case-insensitively, so 4k and 4K are one
            # family; keeping the letter verbatim split it in two.
            parts.append("K")
            index += 1
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
        if char == "\\" and index + 1 < length and template[index + 1] in (SLOT, "\\"):
            literal.append(template[index + 1])
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


# A family at or above this share of members carrying an EPG identifier is more
# likely to be an ordinary numbered channel lineup than a provider event slot,
# so the readout warns before suggesting a pattern for it. MEASURED: a pattern
# over such a family REPLACES the name that is matching today with whatever
# programme is airing, which changes as programming does, whereas a pattern over
# a family carrying no identifiers cannot change matching at all.
MOSTLY_EPG_SHARE = 0.5


def _mostly_carries_epg(family):
    """True when at least MOSTLY_EPG_SHARE of the family's streams carry an
    EPG identifier."""
    count = family.get("count") or 0
    if not count:
        return False
    return (family.get("with_epg_id") or 0) >= MOSTLY_EPG_SHARE * count


def _has_epg_identifier(stream):
    return bool((stream.get("tvg_id") or "").strip())


def scan_families(streams, patterns, on_yield=None, yield_every=YIELD_EVERY,
                  max_name_len=None, budget_seconds=None, stats=None):
    """Group `streams` by template and describe every numbered family found.

    `patterns` is the list of compiled placeholder patterns already configured.
    Coverage is decided with `fullmatch`, the same way the matcher itself
    decides whether a name is a placeholder, so an unanchored pattern that
    would merely search-match does not count as covering anything.

    Returns a list of dicts, best candidate first, ranked by how many members
    carry an EPG identifier and then by size. A family is reported whether or
    not it is covered; the caller decides what to show.

    RUNTIME CONTAINMENT, because this runs inside a uWSGI worker running gevent
    where a loop that never yields freezes the whole worker and every request on
    it. The pattern safety gate in the plugin deliberately admits patterns that
    can backtrack polynomially, on the stated promise that the runtime bounds
    the input, and that promise has to be kept here as well as on the regex
    pre-processing path:

      `on_yield` is called every `yield_every` names, so the caller can hand
      control back to the hub.

      `max_name_len` skips a name longer than the cap rather than applying up to
      fifty operator-supplied patterns to it.

      `budget_seconds` stops the walk once that much time has been spent.

    A stopped walk is reported as partial through `stats`, never passed off as a
    finished one: a family the walk never reached would otherwise be reported as
    uncovered on no evidence. `stats`, when a dict is supplied, receives
    `scanned`, `skipped_long` and `budget_tripped`.
    """
    grouped = {}
    scanned = 0
    skipped_long = 0
    budget_tripped = False
    deadline = (time.monotonic() + budget_seconds) if budget_seconds is not None else None

    for index, stream in enumerate(streams):
        if on_yield is not None and index and index % yield_every == 0:
            on_yield(index)
        if deadline is not None and time.monotonic() >= deadline:
            budget_tripped = True
            break
        # The rows come from whatever the database returned. A row that is not a
        # dict must be skipped, not raise: an exception out of this walk reaches
        # the operator as a stack trace instead of a readout.
        name = stream.get("name") if isinstance(stream, dict) else None
        name = name or ""
        if not name:
            continue
        if max_name_len is not None and len(name) > max_name_len:
            skipped_long += 1
            continue
        scanned += 1
        template, numbers = template_of(name)
        if not numbers:
            continue
        family = grouped.setdefault(template, {
            "template": template,
            "count": 0,
            "slots": None,
            "example": name,
            "with_epg_id": 0,
            "covered": 0,
        })
        family["count"] += 1
        # Distinct numbers are counted WITHIN a slot, not across slots. Counting
        # combinations let a two-slot template with two values in each slot show
        # four "different numbers" while neither slot held three, which is not
        # what the threshold or the readout says.
        if family["slots"] is None:
            family["slots"] = [set() for _ in numbers]
        for position, value in enumerate(numbers):
            if position < len(family["slots"]):
                family["slots"][position].add(value)
        if name < family["example"]:
            family["example"] = name
        if _has_epg_identifier(stream):
            family["with_epg_id"] += 1
        if patterns and any(p.fullmatch(name) for p in patterns):
            family["covered"] += 1

    if isinstance(stats, dict):
        stats["scanned"] = scanned
        stats["skipped_long"] = skipped_long
        stats["budget_tripped"] = budget_tripped

    families = []
    for family in grouped.values():
        distinct = max((len(slot) for slot in family["slots"] or []), default=0)
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
    # Largest first. The EPG identifier count is NOT a ranking key. It was, and
    # MEASURED on 25,068 live stream names that ranked 16 families above the
    # other 115, of which exactly one held a stream whose identifier matched a
    # guide row and none could resolve a programme at all, while the largest
    # family on the installation, at 273 streams, was pushed to a single line.
    # The signal is kept as an annotation on each family, where it says what it
    # actually is.
    families.sort(key=lambda f: (-f["count"], f["template"]))
    return families


def render_report(families, total_streams, pattern_count, feature_enabled,
                  stats=None):
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

    stats = stats if isinstance(stats, dict) else {}
    if stats.get("skipped_long"):
        lines += [
            f"NOTE: {stats['skipped_long']} name(s) were too long to scan and were",
            "skipped. A very long name is not read, because a pattern can take a long",
            "time over one and this runs inside a request.",
            "",
        ]
    if stats.get("budget_tripped"):
        lines += [
            "NOTE: the scan did not finish. It stopped at its time limit, so the",
            "figures above cover only the names it reached and a family it never",
            "reached is missing rather than covered. Run it again when the server is",
            "quieter, or simplify the placeholder patterns, which are applied to every",
            "name.",
            "",
        ]

    if not feature_enabled:
        lines += [
            "NOTE: EPG-Based Placeholder Matching is OFF, so the pattern list is",
            "never consulted and no family is covered in practice, whatever is",
            "written in it. Turn the feature on for these patterns to do anything.",
            "",
        ]

    too_long = [f for f in families if len(f["suggested"]) > SUGGESTION_MAX_LEN]
    if too_long:
        lines.append(f"{len(too_long)} of the suggested patterns are too long for the")
        lines.append("Placeholder Name Patterns setting, which refuses anything over")
        lines.append(f"{SUGGESTION_MAX_LEN} characters. Those are marked below and would")
        lines.append("be skipped in silence if pasted. Shorten the name or write a")
        lines.append("shorter pattern by hand.")
        lines.append("")

    if uncovered:
        word = "family" if len(uncovered) == 1 else "families"
        lines.append(f"{len(uncovered)} numbered {word} that no pattern of yours covers,")
        lines.append("largest first. Size is the ordering because a large family is the")
        lines.append("one most likely to be worth your attention, and because it is what")
        lines.append("a person auditing the list by hand would sort by.")
        lines.append("")
        lines.append("Each entry says how many of its streams carry an EPG identifier.")
        lines.append("Read that as a note, not as a score. A family carrying none cannot")
        lines.append("be resolved from guide data today, and adding its pattern changes")
        lines.append("nothing until that data arrives, which costs nothing meanwhile. A")
        lines.append("family where most streams carry one is more likely to be an ordinary")
        lines.append("numbered channel lineup than an event slot, and a pattern there")
        lines.append("REPLACES a working name with whatever is airing. Those are marked.")
        lines.append("")
        for family in uncovered[:DETAIL_LIMIT]:
            lines.append("  " + ascii_safe(family["template"]))
            lines.append(f"      streams                : {family['count']}"
                         f" ({family['uncovered']} not covered)")
            lines.append(f"      different slot numbers : {family['distinct_numbers']}")
            lines.append(f"      carrying an EPG id     : {family['with_epg_id']}"
                         f" of {family['count']}")
            lines.append("      example                : " + ascii_safe(family["example"]))
            suffix = ("   (TOO LONG for the setting, it will be skipped)"
                      if len(family["suggested"]) > SUGGESTION_MAX_LEN else "")
            lines.append(f"      pattern to paste       : {family['suggested']}{suffix}")
            if _mostly_carries_epg(family):
                lines.append("      CAUTION                : most of these streams carry an")
                lines.append("                               EPG identifier, so this looks like")
                lines.append("                               a numbered channel lineup rather")
                lines.append("                               than an event slot. A pattern here")
                lines.append("                               replaces the name that is matching")
                lines.append("                               today with the programme airing,")
                lines.append("                               which changes as programming does.")
            lines.append("")
        if len(uncovered) > DETAIL_LIMIT:
            lines.append(f"  and {len(uncovered) - DETAIL_LIMIT} more, not described here.")
            lines.append("")
    else:
        lines.append("No uncovered numbered families were found.")
        lines.append("")

    if covered:
        lines.append("Families your patterns already cover in full:")
        for family in covered[:DETAIL_LIMIT * 4]:
            lines.append("  " + ascii_safe(family["template"]) + f"   ({family['count']} streams)")
        if len(covered) > DETAIL_LIMIT * 4:
            lines.append(f"  and {len(covered) - DETAIL_LIMIT * 4} more, not listed here.")
        lines.append("")

    return "\n".join(lines)
