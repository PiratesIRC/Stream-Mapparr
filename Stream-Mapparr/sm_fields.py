"""Settings form layout for Dispatcharr 0.34 and later.

Dispatcharr 0.34.0 added collapsible ``section`` fields, ``multiselect`` pick
lists, ``table`` fields and a ``rows`` height for ``text`` fields. All of them
need ``manifest_version`` 1 in ``plugin.json``, and Dispatcharr 0.33 and earlier
drop a field of a type they do not know with only a log warning. So the plugin
keeps its flat form in ``Plugin.fields``, which every version reads, and builds
the 0.34 form here for ``Plugin.get_fields``, which only 0.34 and later call.

Nine text settings and the Channel Database select are drawn as pick lists
and tables under NEW ids
(``LIST_SETTINGS``, ``TABLE_SETTINGS``). A multiselect calls ``.map`` on its
value, so a stored text value under the same id would break the form.
``legacy_settings`` writes the new values back into the old text settings at run
time, so every parser and validator in the plugin keeps reading one text form.

Pure functions, no Django: unit-tested in ``tests/test_sm_fields.py``.
"""

import copy
import json

# --------------------------------------------------------------------------
# Pick lists: new id -> (old id, separator the old text form uses)
# --------------------------------------------------------------------------
LIST_SETTINGS = {
    "channel_group_list": ("selected_groups", ", "),
    "stream_group_list": ("selected_stream_groups", ", "),
    "m3u_source_list": ("selected_m3us", ", "),
    "scheduled_time_list": ("scheduled_times", ","),
    "channel_database_list": ("channel_database", ","),
}

# Channel Database: the 0.33 select stores one value, "_all", "_none" or a
# country code. The pick list stores a list, written back as "_all" when empty
# (every database), "_none", or the codes joined by commas, which
# Plugin._resolve_enabled_databases reads.
DATABASE_ALL = "_all"
DATABASE_NONE = "_none"
DATABASE_NONE_LABEL = "None - no channel database"


def database_list_from_text(text):
    """The pick-list value for a saved Channel Database setting."""
    codes = split_list(text)
    if not codes or DATABASE_ALL in codes:
        return []
    named = [c for c in codes if c != DATABASE_NONE]
    return named or [DATABASE_NONE]


def database_text_from_list(values):
    """The Channel Database setting for a pick-list value."""
    codes = split_list(values)
    if not codes or DATABASE_ALL in codes:
        return DATABASE_ALL
    named = [c for c in codes if c != DATABASE_NONE]
    return ",".join(named) if named else DATABASE_NONE

# --------------------------------------------------------------------------
# Tables: new id -> (old id, kind). The kind picks the converter pair.
# --------------------------------------------------------------------------
TABLE_SETTINGS = {
    "custom_alias_rows": ("custom_aliases", "aliases"),
    "stream_prefix_country_rows": ("stream_prefix_countries", "prefixes"),
    "stream_name_regex_rows": ("stream_name_regex_rules", "regex"),
    "epg_title_cleanup_rows": ("epg_title_cleanup_rules", "regex"),
    "epg_channel_schedule_cleanup_rows": ("epg_channel_schedule_cleanup_rules", "regex"),
}

NEW_ID_BY_OLD_ID = {spec[0]: new for new, spec in LIST_SETTINGS.items()}
NEW_ID_BY_OLD_ID.update({spec[0]: new for new, spec in TABLE_SETTINGS.items()})

# The M3U Sources entry that stands for every source not named (plugin.py
# M3U_WILDCARD). It is always offered so a priority list can end with it.
M3U_WILDCARD = "*"
M3U_WILDCARD_LABEL = "* (every other source, in the position you put it)"

# Scheduled run times offered in the pick list: every five minutes. A time
# already saved at another minute is added to the list so it still shows.
SCHEDULE_STEP_MINUTES = 5

NEW_FIELDS = {
    "channel_group_list": {
        "label": "📁 Channel Groups",
        "help_text": "The channel groups this plugin should act on. Leave empty for all groups. How the list is read depends on the Channel Groups Mode setting directly below. The list holds the groups that contain at least one channel and is read when the plugin loads: after creating a group, press the refresh control on the Plugins page to see it here.",
    },
    "stream_group_list": {
        "label": "📺 Stream Groups",
        "help_text": "The stream groups to draw candidate streams from when matching. Leave empty for all stream groups. How the list is read depends on the Stream Groups Mode setting directly below. The list holds the groups that contain at least one stream and is read when the plugin loads: after an M3U refresh adds a group, press the refresh control on the Plugins page to see it here.",
    },
    "m3u_source_list": {
        "label": "📡 M3U Sources (in priority order)",
        "help_text": "The M3U sources to use when matching, or leave empty for all of them. The order you pick them in is the priority order: streams from an earlier source are placed before streams from a later one when sorting by quality. To change the order, remove a source and pick it again, which moves it to the end. Without the * entry, Match and Assign uses only the sources picked. Pick * to stand for every other source, so nothing is left out: picking Free Provider and then * puts Free Provider first and every other source after it, including sources added later. The list is read when the plugin loads: after adding an M3U account, press the refresh control on the Plugins page to see it here.",
    },
    "channel_database_list": {
        "label": "Channel Database",
        "help_text": "Which countries' channel lists to load for name and callsign matching. Pick one or more, or leave empty for every database; pick None to load no list. This also decides a channel's country, which is the signal Restrict Matching To Same Country filters on, so a selection that does not cover the channels you are processing makes that filter drop the streams you wanted to keep.",
    },
    "scheduled_time_list": {
        "label": "⏰ Scheduled Run Times",
        "help_text": "Times of day to run automatically, in Dispatcharr's configured Time Zone (General Settings). Pick one or more; type part of a time, for example 06:, to narrow the list. Leave empty to disable scheduling. Click Save Schedule after changing this.",
    },
    "custom_alias_rows": {
        "label": "🔤 Custom Aliases",
        "help_text": "Extra stream names for a channel. Add one row per alias: the channel name in the first column and a stream name the provider uses for it in the second. A channel with several aliases takes several rows. A stream whose name exactly matches an alias is force-matched to that channel. Validate Settings reports rows that cannot be used. Leave the table empty to use the built-in aliases only.",
        "columns": {"channel": "Channel name", "alias": "Stream name alias"},
    },
    "stream_prefix_country_rows": {
        "label": "🏷️ Stream Prefix Countries",
        "help_text": "Tell the country filter what a provider prefix means. Add one row per prefix, for example NOW and UK. Use this when a prefix names a platform rather than a country, so the plugin cannot know it: NOW is Sky's service in the United Kingdom and also in Italy, which is why no default can be right for everyone. A prefix listed here is only consulted when nothing else identified the stream, so it fills a gap and never overrules a country the provider stated. It matches only at the start of a name, as NOW: or (NOW), never the word inside a title. Leave the table empty to change nothing.",
        "columns": {"prefix": "Prefix", "country": "Country"},
    },
    "stream_name_regex_rows": {
        "label": "🧹 Stream Name Regex Rules",
        "help_text": "Find and replace rules, applied to stream names top row first, before matching. One rule per row: a Python regular expression in Find, and the text to put in its place in Replace (leave Replace empty to delete the match). Type the expression as it is; no JSON escaping is needed here. Use (?i) at the start for case-insensitive. Affects MATCHING only: stream names in Dispatcharr are never modified, and quality sorting, zone routing, country restriction and duplicate detection still read the original name. Use the Test Regex Rules action to preview the effect.",
        "columns": {"find": "Find (regex)", "replace": "Replace with"},
    },
    "epg_title_cleanup_rows": {
        "label": "🧽 EPG Title Cleanup Rules",
        "help_text": "Find and replace rules, in the same form as Stream Name Regex Rules, applied to the raw EPG programme title before it is used for matching. For example, a rule can strip a 'Next Event: X at 6:00AM on Jul 26' wrapper down to just 'X'.",
        "columns": {"find": "Find (regex)", "replace": "Replace with"},
    },
    "epg_channel_schedule_cleanup_rows": {
        "label": "🗓️ Channel Schedule Suffix Cleanup Rules",
        "help_text": "Find and replace rules, in the same form as Stream Name Regex Rules, applied to the CHANNEL name (not the stream) whenever EPG-based placeholder matching is enabled, before it is compared against a resolved EPG title. The default rule strips a trailing '| Monday @ 5' schedule note, so 'WWE Monday Night Raw | Monday @ 5' compares as 'WWE Monday Night Raw'. Adjust or clear it if your naming convention differs.",
        "columns": {"find": "Find (regex)", "replace": "Replace with"},
    },
}

# Dispatcharr draws help text as one plain paragraph: no line breaks, no
# Markdown. In the 0.34 form a long help text is cut to its first sentences and
# the rest follows as titled info fields, one topic each, right below the field.
# Keyed by the field id in the 0.34 form. Each value is (short help,
# [(title, paragraph), ...]). The flat form keeps the full text.
HELP_PARAGRAPHS = {
    "group_filter_mode": (
        "Decides whether the Channel Groups list names the groups to process or the groups to skip. Leaving Channel Groups empty processes every group either way.",
        [
            ("When to skip groups", "Use the second option when another tool owns a group and this plugin should leave it alone, for example a static group holding event channels created elsewhere. It also means a channel group you create later is processed automatically, whereas listing groups to process means a new group is skipped until you add it."),
            ("Unknown group names", "A group name that does not exist is reported as an error by Validate Settings in both modes, because a typo in the second mode would process the group you meant to skip."),
        ],
    ),
    "restrict_matching_to_country": (
        "When enabled, channels only match streams detected as the same country. When disabled, the older cross-country matching is used.",
        [
            ("How a country is detected", "Detection checks the channel database entry first, then the group name, then the channel name (bare, '|'-delimited or multi-token country markers all count). Streams with no recognizable country marker are not dropped; they are kept as lower-priority alternates behind same-country matches."),
            ("Same-named channels", "Same-named channels for different countries (for example a CANADA and a UK channel both named CNN) are matched separately instead of being merged. OTA broadcast channels (matched by FCC callsign) are exempt from this filter."),
            ("Choosing a Channel Database", "Works best with a single Channel Database selected. With 'All', a channel name that appears in several databases with disagreeing countries (for example CNN, TNT or USA Network) is treated as ambiguous, and the filter is skipped for that channel rather than guessing."),
        ],
    ),
    "stream_prefix_country_rows_about": (
        "Tell the country filter what a provider prefix means. Add one row per prefix, for example NOW and UK. Leave the table empty to change nothing.",
        [
            ("Why this is a setting", "A prefix often names a platform rather than a country, so the plugin cannot know it: NOW is Sky's service in the United Kingdom and also in Italy, which is why no default can be right for everyone."),
            ("When a prefix is used", "A prefix listed here is only consulted when nothing else identified the stream, so it fills a gap and never overrules a country the provider stated. It matches only at the start of a name, as NOW: or (NOW), never the word inside a title."),
        ],
    ),
    "epg_watch_source_streams": (
        "Exact stream names (case-insensitive, comma-separated) of permanently named channels, such as ESPN, whose programming sometimes carries an event that has no placeholder stream of its own. Empty disables this (the default). Requires EPG-based placeholder matching to be enabled above.",
        [
            ("What it does", "A listed stream becomes an EXTRA alternate stream for any channel whose full cleaned name is contained in that stream's current EPG programme title. For example, channel 'WWE SummerSlam' matches while ESPN's guide currently reads 'WWE SummerSlam Special'."),
            ("What it leaves alone", "A watched stream keeps matching its own literally named channel exactly as before. It is added as a candidate elsewhere only while its current programme title contains the target channel's name."),
        ],
    ),
    "epg_channel_schedule_cleanup_rows_about": (
        "Find and replace rules, in the same form as Stream Name Regex Rules, applied to the CHANNEL name (not the stream) whenever EPG-based placeholder matching is enabled, before it is compared against a resolved EPG title.",
        [
            ("The default rule", "Without provider-assigned channel numbers, a common convention is to name event channels with an inline schedule, for example 'WWE Monday Night Raw | Monday @ 5'. The default rule strips that trailing '| Weekday @ Time' note so the channel compares as 'WWE Monday Night Raw' against the real EPG title. Adjust or clear it if your naming convention differs."),
        ],
    ),
    "csv_export_retention_days": (
        "After each report is written, delete this plugin's older reports from /data/exports. Zero, the default, keeps everything.",
        [
            ("What is never deleted", "Only files this plugin wrote are ever considered: that directory is shared with several other plugins and their reports are never touched. The newest report always survives, and so does the one just written, so a small number here cannot empty the directory."),
            ("How age is measured", "Age is taken from the file's modification time. This applies to manual runs as well as scheduled ones; the Clear CSV Exports button still clears everything regardless of this setting."),
        ],
    ),
}
HELP_SUFFIX = "_help_"

# The ``info`` fields that head each group of settings in the flat form.
SECTION_PREFIX = "_section_"

# Groups a typical user rarely opens start closed.
COLLAPSED_SECTIONS = frozenset((
    "_section_notifications",
    "_section_epg_matching",
    "_section_iptv_checker",
    "_section_throughput",
    "_section_usage",
))

# Starting height, in lines, of text fields left as text.
TEXT_ROWS = {
    "epg_placeholder_name_patterns": 4,
}

# Id suffix of the info field that keeps a section header's or a table's
# explanation (a section marker and a table draw only their label).
ABOUT_SUFFIX = "_about"


class UnconvertibleValue(ValueError):
    """A saved text value that a table cannot hold without losing part of it."""


def is_section_header(field):
    """True for an ``info`` field whose id marks the start of a settings group."""
    return (isinstance(field, dict) and field.get("type") == "info"
            and str(field.get("id", "")).startswith(SECTION_PREFIX))


def split_list(text):
    """A comma-separated setting as a list of trimmed, non-empty entries."""
    if isinstance(text, (list, tuple)):
        return [str(v).strip() for v in text if str(v).strip()]
    if text is None:
        return []
    return [part.strip() for part in str(text).split(",") if part.strip()]


# --------------------------------------------------------------------------
# Text form <-> table rows, one pair per table kind
# --------------------------------------------------------------------------
def _cell(row, key):
    value = row.get(key) if isinstance(row, dict) else None
    return "" if value is None else str(value)


def alias_rows_from_text(text):
    """Custom Aliases JSON as rows of {channel, alias}, one row per alias.

    Raises UnconvertibleValue for anything the table could not hold exactly:
    invalid JSON, a top level that is not an object, or a value that is not a
    string or a list of strings. The caller then keeps the text box.
    """
    text = "" if text is None else str(text)
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UnconvertibleValue(f"not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UnconvertibleValue("top level is not a JSON object")
    rows = []
    for channel, value in data.items():
        aliases = [value] if isinstance(value, str) else value
        if not isinstance(aliases, list) or not all(isinstance(a, str) for a in aliases):
            raise UnconvertibleValue(f"value for {channel!r} is not a string or a list of strings")
        if not aliases:
            rows.append({"channel": channel, "alias": ""})
        for alias in aliases:
            rows.append({"channel": channel, "alias": alias})
    return rows


def alias_text_from_rows(rows):
    """Rows of {channel, alias} as the Custom Aliases JSON object.

    Rows of one channel are grouped in first-seen order. A row with both cells
    empty is dropped; a row with only the channel keeps an empty alias list so
    Validate Settings reports it.
    """
    grouped = {}
    for row in rows or []:
        channel = _cell(row, "channel").strip()
        alias = _cell(row, "alias").strip()
        if not channel and not alias:
            continue
        aliases = grouped.setdefault(channel, [])
        if alias:
            aliases.append(alias)
    if not grouped:
        return ""
    return json.dumps(grouped, ensure_ascii=False)


def prefix_rows_from_text(text):
    """'PREFIX=CC' entries (comma or newline separated) as {prefix, country}
    rows. A comment after '#' is dropped, as the parser drops it. An entry with
    no '=' keeps its text in the prefix column and no country."""
    rows = []
    text = "" if text is None else str(text)
    for chunk in text.replace(",", "\n").splitlines():
        line = chunk.split("#", 1)[0].strip()
        if not line:
            continue
        prefix, sep, code = line.partition("=")
        code = code.strip().upper() if sep else ""
        rows.append({"prefix": prefix.strip().upper(), "country": code or None})
    return rows


def prefix_text_from_rows(rows):
    """{prefix, country} rows as 'PREFIX=CC, PREFIX=CC'. A row with both cells
    empty is dropped. A prefix holding a separator the text form uses (a comma,
    an '=' or a '#') cannot be written and is dropped."""
    entries = []
    for row in rows or []:
        prefix = _cell(row, "prefix").strip().upper()
        country = _cell(row, "country").strip().upper()
        if not prefix and not country:
            continue
        if any(c in prefix for c in ",=#\n"):
            continue
        entries.append(f"{prefix}={country}")
    return ", ".join(entries)


def regex_rows_from_text(text):
    """A [[find, replace], ...] JSON setting as {find, replace} rows.

    Raises UnconvertibleValue when any entry is not a pair of strings, because
    the table could not show it and a save would then drop it.
    """
    text = "" if text is None else str(text)
    if not text.strip():
        return []
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise UnconvertibleValue(f"not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise UnconvertibleValue("top level is not a JSON array")
    rows = []
    for entry in data:
        if not (isinstance(entry, list) and len(entry) == 2
                and all(isinstance(x, str) for x in entry)):
            raise UnconvertibleValue("an entry is not a [find, replace] pair of strings")
        rows.append({"find": entry[0], "replace": entry[1]})
    return rows


def regex_text_from_rows(rows):
    """{find, replace} rows as the [[find, replace], ...] JSON text. A row with
    both cells empty is dropped; a row with an empty find is kept so the parser
    reports it. Cells are kept exactly as typed, spaces included, because a
    space can be the whole point of a pattern."""
    pairs = []
    for row in rows or []:
        find = _cell(row, "find")
        replace = _cell(row, "replace")
        if not find and not replace:
            continue
        pairs.append([find, replace])
    if not pairs:
        return ""
    return json.dumps(pairs, ensure_ascii=False)


ROWS_FROM_TEXT = {
    "aliases": alias_rows_from_text,
    "prefixes": prefix_rows_from_text,
    "regex": regex_rows_from_text,
}
TEXT_FROM_ROWS = {
    "aliases": alias_text_from_rows,
    "prefixes": prefix_text_from_rows,
    "regex": regex_text_from_rows,
}


def legacy_settings(settings):
    """Return a copy of ``settings`` with every pick list or table value written
    back into its old text setting, so the rest of the plugin reads one form.

    Only a new id holding a LIST counts; anything else leaves the old setting
    alone. A 0.34 save stores the new ids and leaves the old ids as they were
    (Dispatcharr keeps stored keys that are not in the form), so the new value
    must win. Dispatcharr fills a new id that was never saved with its field
    default, which get_fields builds from the old setting, so the two agree.
    Idempotent: applying it twice gives the same result.
    """
    out = dict(settings or {})
    for new_id, (old_id, separator) in LIST_SETTINGS.items():
        value = out.get(new_id)
        if not isinstance(value, list):
            continue
        if new_id == "channel_database_list":
            out[old_id] = database_text_from_list(value)
        else:
            out[old_id] = separator.join(split_list(value))
    for new_id, (old_id, kind) in TABLE_SETTINGS.items():
        value = out.get(new_id)
        if isinstance(value, list):
            out[old_id] = TEXT_FROM_ROWS[kind](value)
    return out


# --------------------------------------------------------------------------
# Building the 0.34 form
# --------------------------------------------------------------------------
def _options(names, extra=(), forbidden=""):
    """Sorted pick-list options from ``names`` plus every ``extra`` value.

    Duplicates are dropped by EXACT spelling: the plugin compares group and
    source names case-sensitively, so 'sports' and 'Sports' are different
    entries, and folding them would leave a saved value with no option, which
    makes Dispatcharr reject the save. A name holding a character in
    ``forbidden`` is left out, because the text form it is written back into
    could not hold it. An ``extra`` value is never left out.
    """
    seen = []
    known = set()
    for name in names or ():
        name = str(name or "").strip()
        if name and name not in known and not any(c in name for c in forbidden):
            known.add(name)
            seen.append(name)
    for name in extra or ():
        name = str(name or "").strip()
        if name and name not in known:
            known.add(name)
            seen.append(name)
    return [{"label": n, "value": n} for n in sorted(seen, key=lambda s: (s.casefold(), s))]


def _schedule_options(extra=()):
    options = []
    known = set()
    for minute in range(0, 24 * 60, SCHEDULE_STEP_MINUTES):
        value = f"{minute // 60:02d}{minute % 60:02d}"
        known.add(value)
        options.append({"label": f"{value[:2]}:{value[2:]}", "value": value})
    for value in extra or ():
        if value not in known:
            known.add(value)
            options.append({"label": value, "value": value})
    return options


def _stored_list(settings, new_id):
    value = (settings or {}).get(new_id)
    return split_list(value) if isinstance(value, list) else []


def _stored_rows(settings, new_id):
    value = (settings or {}).get(new_id)
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _database_options(choices, extra=()):
    """The databases the select offers, by label, plus None, plus any saved
    code with no database file (a saved value with no option makes Dispatcharr
    reject the save). The select's All entry is dropped: empty means all."""
    options, known = [], set()
    for choice in choices or ():
        value = str((choice or {}).get("value") or "").strip()
        if not value or value in known or value in (DATABASE_ALL, DATABASE_NONE):
            continue
        known.add(value)
        options.append({"label": str(choice.get("label") or value), "value": value})
    for value in extra or ():
        if value not in known and value not in (DATABASE_ALL, DATABASE_NONE):
            known.add(value)
            options.append({"label": value, "value": value})
    options.append({"label": DATABASE_NONE_LABEL, "value": DATABASE_NONE})
    return options


def _list_field(new_id, default, stored, names, choices=()):
    meta = NEW_FIELDS[new_id]
    used = default + stored
    if new_id == "scheduled_time_list":
        options = _schedule_options(used)
    elif new_id == "channel_database_list":
        options = _database_options(choices, used)
    else:
        options = _options(names.get(new_id, ()), used, forbidden=",")
        if new_id == "m3u_source_list":
            # The wildcard is offered first, with a label saying what it does.
            options = [o for o in options if o["value"] != M3U_WILDCARD]
            options.insert(0, {"label": M3U_WILDCARD_LABEL, "value": M3U_WILDCARD})
    return {
        "id": new_id, "label": meta["label"], "type": "multiselect",
        "default": default, "options": options, "help_text": meta["help_text"],
    }


def _table_columns(new_id, kind, default, stored, country_codes):
    labels = NEW_FIELDS[new_id]["columns"]
    if kind == "aliases":
        return [{"id": "channel", "label": labels["channel"], "type": "string"},
                {"id": "alias", "label": labels["alias"], "type": "string"}]
    if kind == "prefixes":
        used = [r.get("country") for r in default + stored if r.get("country")]
        return [{"id": "prefix", "label": labels["prefix"], "type": "string"},
                {"id": "country", "label": labels["country"], "type": "select",
                 "options": _options(country_codes, used)}]
    return [{"id": "find", "label": labels["find"], "type": "string"},
            {"id": "replace", "label": labels["replace"], "type": "string"}]


def pick_list_fields(flat_fields, settings=None, names=None, country_codes=(), logger=None):
    """Return the flat form with the ten settings above replaced by pick lists
    and tables under their new ids. The input is not modified.

    ``settings`` are the saved settings; a field's default is built from the old
    text setting, so an install that never saved the new field keeps its values.
    ``names`` maps a pick-list id to the names its options come from.
    ``country_codes`` fills the Stream Prefix Countries country column.

    A table whose saved text cannot be converted without loss (invalid JSON, an
    entry of the wrong shape) is left as its text box under the old id, so the
    operator sees and can fix the value rather than lose it on the next save.
    """
    settings = settings or {}
    names = names or {}
    out = []
    for field in flat_fields or []:
        if not isinstance(field, dict) or field.get("id") not in NEW_ID_BY_OLD_ID:
            out.append(copy.deepcopy(field) if isinstance(field, dict) else field)
            continue
        old_id = field["id"]
        new_id = NEW_ID_BY_OLD_ID[old_id]
        old_value = settings.get(old_id, field.get("default", ""))

        if new_id == "channel_database_list":
            out.append(_list_field(new_id, database_list_from_text(old_value),
                                   _stored_list(settings, new_id), names,
                                   choices=field.get("options") or ()))
            continue
        if new_id in LIST_SETTINGS:
            out.append(_list_field(new_id, split_list(old_value),
                                   _stored_list(settings, new_id), names))
            continue

        kind = TABLE_SETTINGS[new_id][1]
        try:
            default = ROWS_FROM_TEXT[kind](old_value)
        except UnconvertibleValue as exc:
            if logger is not None:
                logger.warning(f"[Stream-Mapparr] {old_id} kept as a text box in the settings "
                               f"form: the saved value cannot be shown as a table ({exc})")
            out.append(copy.deepcopy(field))
            continue
        stored = _stored_rows(settings, new_id)
        meta = NEW_FIELDS[new_id]
        # A table draws only its label, not its help text, so the explanation
        # goes in an info field above it and the table itself has no label.
        out.append({"id": new_id + ABOUT_SUFFIX, "label": meta["label"], "type": "info",
                    "description": meta["help_text"]})
        out.append({"id": new_id, "label": "", "type": "table",
                    "columns": _table_columns(new_id, kind, default, stored, country_codes),
                    "default": default})
    return out


def sectioned_fields(flat_fields):
    """Return the flat form rearranged into collapsible sections.

    Each section header becomes a ``section`` marker with the same id, and every
    field after it, up to the next header, gets ``"section": <that id>``. A
    header's explanation moves to an ``info`` field at the top of its section,
    because a marker shows only its label. Fields before the first header stay
    ungrouped (Dispatcharr draws ungrouped fields above every section). The
    input is not modified.
    """
    out = []
    current = None
    for field in flat_fields or []:
        if not isinstance(field, dict):
            continue
        if is_section_header(field):
            current = field["id"]
            out.append({
                "id": current,
                "label": field.get("label") or current,
                "type": "section",
                "collapsed": current in COLLAPSED_SECTIONS,
            })
            text = field.get("description") or field.get("help_text")
            if text:
                out.append({
                    "id": current + ABOUT_SUFFIX,
                    "label": "",
                    "type": "info",
                    "description": text,
                    "section": current,
                })
            continue
        item = copy.deepcopy(field)
        if current is not None:
            item["section"] = current
        if item.get("type") == "text" and item.get("id") in TEXT_ROWS:
            item["rows"] = TEXT_ROWS[item["id"]]
        out.append(item)
        out.extend(_split_help(item, current))
    return out


def _split_help(item, section):
    """Cut a long help text to its short form (in place) and return the rest as
    titled info fields that follow the field. An info field's text is in
    ``description``; any other field's is in ``help_text``."""
    spec = HELP_PARAGRAPHS.get(item.get("id"))
    if not spec:
        return []
    short, paragraphs = spec
    item["description" if item.get("type") == "info" else "help_text"] = short
    extra = []
    for number, (title, text) in enumerate(paragraphs, start=1):
        info = {"id": f"{item['id']}{HELP_SUFFIX}{number}", "label": title,
                "type": "info", "description": text}
        if section is not None:
            info["section"] = section
        extra.append(info)
    return extra


def build_form(flat_fields, settings=None, names=None, country_codes=(), logger=None):
    """The whole 0.34 form: pick lists and tables, then sections."""
    return sectioned_fields(pick_list_fields(flat_fields, settings, names, country_codes, logger))
