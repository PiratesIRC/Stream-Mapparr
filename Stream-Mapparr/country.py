"""Country detection for Stream-Mapparr's "Restrict Matching To Same Country" filter.

Pure, stdlib-only, no Django — a vocabulary sibling of aliases.py.

DESIGN NOTE (bug-159, read before widening anything here): this module is
FAIL-OPEN. A token it does not recognize yields None, which the caller treats as
UNKNOWN and KEEPS. An earlier design treated an unrecognized country-SHAPED prefix
as FOREIGN; simulated against 2,439 real stream names that dropped 924 matches and
left 121 channels (ESPN, C-SPAN, Big Ten, Tennis Channel...) with zero streams,
because "GO:" (681 occurrences) and "RK:" (258) are FAST distribution-platform
tags on US channels, not countries. Never make this module fail closed.

Cross-reference: matching_core.py PROVIDER_PREFIX_PATTERNS strips these same
prefixes for MATCHING. The two encode "what a country prefix looks like"
independently and can drift — change them together.
"""

import re

SAME = "SAME"
FOREIGN = "FOREIGN"
UNKNOWN = "UNKNOWN"

# ISO-2 codes we are willing to name. Seeded from Lineuparr's battle-grown set
# (Lineuparr/Lineuparr/fuzzy_matcher.py), which grew from real feeds leaking onto
# globally-named channels (CNN, BBC) in a US lineup. All 12 shipped channel
# databases are present.
KNOWN_COUNTRY_CODES = {
    "US", "UK", "CA", "AU", "DE", "FR", "IT", "ES", "NL", "BR", "MX", "IN",
    "IE", "SE", "NO", "DK", "PT", "PL", "AT", "CH", "BE", "FI",
    "TR", "GR", "IR", "AL",
    "BG", "RO", "RU", "AZ", "HR", "TH", "RS", "MK", "IL", "CO", "CR", "CY",
    "JP", "KR", "CZ", "HU", "NZ", "PH",
    # bug-158: the reporter's provider tags Argentina as "ARG:". Lineuparr
    # deliberately excludes AR because in ITS feeds AR tags Arabic-language
    # channels; both readings resolve to "not the channel's country" for all 12
    # shipped databases, so naming it is safe here. Revisit if an AR database ships.
    "AR",
    "DO",
}

ISO3_TO_ISO2 = {
    "USA": "US", "GBR": "UK", "CAN": "CA", "AUS": "AU", "DEU": "DE", "GER": "DE",
    "FRA": "FR", "ITA": "IT", "ESP": "ES", "NLD": "NL", "BRA": "BR", "MEX": "MX",
    "IND": "IN", "IRL": "IE", "SWE": "SE", "NOR": "NO", "DNK": "DK", "PRT": "PT",
    "POL": "PL", "AUT": "AT", "CHE": "CH", "BEL": "BE", "FIN": "FI",
    "ARG": "AR", "DOM": "DO", "NZL": "NZ", "JPN": "JP", "KOR": "KR",
}

# Two-letter forms that mean the same country as a code already in use, folded
# onto the code the shipped channel databases are keyed by.
#
# Reported by a user: providers disagree about which code a country gets. Some
# prefix United Kingdom channels with the official ISO code GB and others with
# the common but unofficial UK, and the same split exists for the Dominican
# Republic between the official DO and the widely used DR. Before this, UK and
# GBR resolved while GB did not, and DO and DOM resolved while DR did not.
#
# The target is the DATABASE code, not the official one. UK_channels.json is
# keyed "UK", so GB has to fold onto UK rather than introduce a third code that
# the country filter and the channel database would then disagree about. This
# is also why no database needs duplicating: a database is chosen by the
# operator, never by the provider's prefix.
#
# Applied AFTER the non-country prefix list, so a platform or quality tag can
# never be captured here.
ISO2_SYNONYMS = {
    "GB": "UK",
    "DR": "DO",
}

# Tokens that occupy the country slot but are NOT countries. Consulted BEFORE the
# whitelist, so a future collision (CH = Switzerland vs "Channel") resolves the
# safe way. Under the fail-open rule this list is belt-and-braces rather than
# load-bearing: an unlisted non-country simply yields None and is kept.
NON_COUNTRY_PREFIXES = {
    # FAST / distribution-platform tags. matching_core.py already documents these
    # as "the distribution platform, not the channel or its country".
    "RK", "GO", "TUBI", "PLUTO", "XUMO", "PLEX", "STIRR", "FREEVEE", "GLANCE",
    # Quality / variant tags seen in this slot ("UK FHD:", "UK HD:", "CA LBW:").
    "HD", "FHD", "UHD", "SD", "HQ", "LQ", "LBW", "RAW", "HEVC", "HDR", "4K", "8K",
    # Bouquet / category.
    "PPV", "VIP", "ADULT", "EROTIC", "PRIME", "GOLD", "NFL", "NBA", "MLB",
    "EVENT", "LIVE", "TV", "XXX",
    # OTA affiliate-network prefixes seen in the real corpus
    # ("FOX: EL PASO TX KFOX", "CBS: FL ORLANDO WKMG").
    "ABC", "CBS", "NBC", "FOX", "PBS", "CW", "ION",
    # Provider/region tags Lineuparr deliberately left out of its whitelist;
    # its comments record that HUB/AMP carry US channels and MT tags theme
    # channels rather than Malta.
    "HUB", "AMP", "STC", "OSN", "MEO", "MXC", "LA", "AFR", "AF", "MT",
}

# Long-form country WORDS, minimum 4 characters, scanned anywhere in a name.
# Deliberately excludes the short forms USA / UK / CA — that exclusion is what
# stops "USA Network" and "CNN USA" self-classifying (bug-158).
GROUP_NAME_ALIASES = {
    "UNITED STATES OF AMERICA": "US", "UNITED STATES": "US",
    "UNITED KINGDOM": "UK", "GREAT BRITAIN": "UK", "BRITAIN": "UK",
    "ENGLAND": "UK", "SCOTLAND": "UK", "WALES": "UK",
    "CANADA": "CA", "AUSTRALIA": "AU", "INDIA": "IN",
    "GERMANY": "DE", "DEUTSCHLAND": "DE", "FRANCE": "FR",
    "NETHERLANDS": "NL", "HOLLAND": "NL", "NORWAY": "NO", "NORGE": "NO",
    "SPAIN": "ES", "ESPANA": "ES", "ESPAÑA": "ES",
    "MEXICO": "MX", "MÉXICO": "MX", "BRAZIL": "BR", "BRASIL": "BR",
    "ARGENTINA": "AR", "PORTUGAL": "PT", "IRELAND": "IE",
}

# Three-letter aliases scanned in GROUP names only. Two-letter codes are never
# whole-word scanned anywhere: "IN"/"CA"/"IT"/"NO" collide with English words.
GROUP_SHORT_ALIASES = {"USA": "US", "GBR": "UK", "CAN": "CA", "AUS": "AU", "MEX": "MX"}

# Codes safe to recognize WITHOUT a delimiter (bare "US CNN", "CA TSN 1 HD").
# Curated: NO/IT/IN/AT/BE/TO/IS/DO are English words at the head of real titles.
_BARE_CODES = "US|UK|CA|AU|FR|DE|MX|MEX|FRA|GER"

_DELIM = r"[-:|┃│]"

_RE_WRAPPED = re.compile(r"^\s*[\(\[\|┃│]\s*([A-Za-z]{2,3})\s*[\)\]\|┃│]")
_RE_TOKEN_DELIM = re.compile(r"^\s*([A-Za-z]{2,3})\s*" + _DELIM)
_RE_MULTI_TOKEN = re.compile(
    r"^\s*(" + _BARE_CODES + r")\s+[A-Za-z0-9]{1,6}\s*" + _DELIM, re.IGNORECASE)
_RE_GLUED_QUALITY = re.compile(
    r"^\s*(US|UK)(?:SD|HD|FHD|UHD|FD|HEVC|4K|8K)\b", re.IGNORECASE)
_RE_BARE_CODE = re.compile(r"^\s*(" + _BARE_CODES + r")\s+", re.IGNORECASE)
_RE_USA_BARE = re.compile(r"^\s*USA\s+(?!NETWORK\b)", re.IGNORECASE)


def _normalize_token(token):
    """Map a raw prefix token to a whitelisted ISO-2 code, else None."""
    if not token:
        return None
    token = token.upper()
    if token in NON_COUNTRY_PREFIXES:
        return None
    # Checked after the non-country list, so a platform or quality tag can never
    # be folded onto a country, and before the whitelist, so the synonym decides
    # the answer rather than the raw token.
    synonym = ISO2_SYNONYMS.get(token)
    if synonym:
        return synonym
    if token in KNOWN_COUNTRY_CODES:
        return token
    mapped = ISO3_TO_ISO2.get(token)
    return mapped if mapped in KNOWN_COUNTRY_CODES else None


def _from_prefix(text):
    """Anchored country marker at the START of `text`, or None.

    Branch order matters: whole-string first, then the most specific marker
    shapes, then the bare/space-separated forms that need a curated token set.
    """
    stripped = text.strip()

    # 0. The value IS a country token ("US", "USA", "UK").
    if re.fullmatch(r"[A-Za-z]{2,3}", stripped):
        return _normalize_token(stripped)

    # 1. Matched-delimiter wrap: (US), [UK], |CA|.
    m = _RE_WRAPPED.match(text)
    if m:
        return _normalize_token(m.group(1))

    # 2. Token + optional space + delimiter: "US:", "US|", "US :", "MEX-".
    m = _RE_TOKEN_DELIM.match(text)
    if m:
        return _normalize_token(m.group(1))

    # 3. Multi-token prefix: "CA LBW:" -> CA. Restricted to the curated set so
    #    "IN PROGRESS:" and "NO LIMIT TV:" cannot be read as countries.
    m = _RE_MULTI_TOKEN.match(text)
    if m:
        return _normalize_token(m.group(1))

    # 4. Country glued to a quality tag: "UKSD:", "USFHD ESPN".
    m = _RE_GLUED_QUALITY.match(text)
    if m:
        return _normalize_token(m.group(1))

    # 5. Bare curated code + whitespace: "US CNN", "CA TSN 1 HD".
    m = _RE_BARE_CODE.match(text)
    if m:
        return _normalize_token(m.group(1))

    # 6. Bare "USA " with the brand guard that protects the channel USA Network.
    if _RE_USA_BARE.match(text):
        return "US"

    return None


def _scan_words(text, table):
    """Longest-alias-first whole-word scan of `text` against `table`."""
    normalized = re.sub(r"[\[\]\(\)_\-|┃│]+", " ", text.upper())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for alias, code in sorted(table.items(), key=lambda kv: len(kv[0]), reverse=True):
        if re.search(r"\b" + re.escape(alias) + r"\b", normalized):
            return code
    return None


def country_from_group(text):
    """Country for a provider CATEGORY label (channel_group__name).

    Group labels are a controlled-ish vocabulary, so they get the wider scan:
    anchored marker, then long-form words, then 3-letter aliases ("USA Networks").
    """
    if not text:
        return None
    code = _from_prefix(str(text))
    if code:
        return code
    combined = dict(GROUP_NAME_ALIASES)
    combined.update(GROUP_SHORT_ALIASES)
    return _scan_words(str(text), combined)


def country_from_name(text):
    """Country for a CHANNEL or STREAM name.

    Names carry brands, so only an anchored marker or a LONG-FORM country word
    counts. "CNN Brasil" -> BR; "CNN USA" and "USA Network" -> None.
    """
    if not text:
        return None
    code = _from_prefix(str(text))
    if code:
        return code
    return _scan_words(str(text), GROUP_NAME_ALIASES)


def classify(channel_code, stream_code):
    """SAME / FOREIGN / UNKNOWN for one channel/stream country pair.

    UNKNOWN whenever either side is unresolved — callers KEEP unknown streams as
    lower-priority alternates. See the fail-open design note at the top.
    """
    if channel_code is None or stream_code is None:
        return UNKNOWN
    return SAME if channel_code == stream_code else FOREIGN
