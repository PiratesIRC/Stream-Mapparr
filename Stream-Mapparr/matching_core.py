"""
matching_core.py - shared pure matching primitives for the Dispatcharr plugins.

The canonical, vendored core extracted from Lineuparr's fuzzy_matcher.py (v1.3.4, the
newest generation). It is PURE and stateless string-in/string-out: stdlib + optional
rapidfuzz only, with no Django / ORM / filesystem coupling. Each plugin SUBCLASSES
FuzzyMatcherCore and layers its own orchestration on top (DB load, matching entry points,
and one feature block: zones / token-index / country / OTA). See
MATCHER-STANDARDIZATION-PLAN.md.

Authored faithfully from Lineuparr with exactly these deliberate changes:
  - class renamed FuzzyMatcher -> FuzzyMatcherCore; Lineuparr's country layer, the
    matching entry points (alias_match / fuzzy_match / match_all_streams), the cache glue
    (precompute_normalizations / _get_cached_*), _channel_number_boost, _is_group_header,
    and has_upgrade_quality are NOT included - they stay plugin-side.
  - process_string_for_matching KEEPS '+' so Disney+ / Discovery+ / Paramount+ stay
    distinct from their base channels (the 4-of-4 superset decision).
  - the callsign confidence ladder consults an optional self._known_callsigns set so a
    plugin can rescue a denylisted-but-real station (KING / WAVE / WOOD / WHO) from its own
    channel DB. With the default (None) the ladder is identical to Lineuparr's pure path.

This file is the SOURCE OF TRUTH. It is vendored (copied byte-identically) into each
plugin's flat inner folder at release time and hash-pinned; never edit a vendored copy -
edit this file and re-sync.
"""

import logging
import re
import unicodedata

# Optional C-accelerated Levenshtein. When present, the matcher uses rapidfuzz's
# normalized_similarity (1 - distance/max(len)); the pure-Python fallback below
# computes the identical value (bug-026). rapidfuzz is an OPTIONAL runtime dep.
try:
    from rapidfuzz.distance import Levenshtein as _rf_lev
    _USE_RAPIDFUZZ = True
except ImportError:
    _USE_RAPIDFUZZ = False

__version__ = "0.1.0"

LOGGER = logging.getLogger("matching_core")
if not LOGGER.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    LOGGER.addHandler(_handler)
# WARNING by default so a vendored core does not spam each plugin's logs with the
# per-name "normalize_name returned empty" debug line; a plugin can pass its own
# logger at a lower level when diagnosing.
LOGGER.setLevel(logging.WARNING)

# --- Pattern categories for normalization ---

# Unicode categories considered decorative/badge characters by IPTV providers.
# So = Other Symbol (◉), No = Other Number (², ³), Lm = Modifier Letter
# (ᴿᴬᵂ, ᴴᴰ, ⱽᴵᴾ superscripts), Sk = Modifier Symbol.
# Accented letters (é, î, ü…) are Ll/Lu and are NOT in this set.
# Sm (Math Symbol) is intentionally EXCLUDED: it contains "+", which is a
# meaningful, channel-distinguishing character (Canal+, Three Stooges+,
# Comedy Central+). Stripping it regresses those matches.
_DECORATOR_CATS = frozenset({'So', 'No', 'Lm', 'Sk'})

# Tokens that are non-distinctive stream-label variants (e.g. "ABC News Live"
# should still match "ABC News"). Used by the subset/divergent guards.
_NON_DISTINCTIVE_TOKENS = frozenset({"live", "now", "new"})

def _is_distinctive(t):
    """Return True if token t is distinctive enough to matter in subset/divergent guards."""
    return t not in _NON_DISTINCTIVE_TOKENS and (len(t) >= 4 or (t.isdigit() and len(t) >= 2))

QUALITY_PATTERNS = [
    r'\s*\[(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead|Backup)\]\s*',
    r'\s*\((4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead|Backup)\)\s*',
    r'^\s*(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead)\b\s*',
    r'\s*\b(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead)$',
    r'\s+\b(4K|8K|UHD|FHD|HD|HDR|HEVC|SD|FD|Unknown|Unk|Slow|Dead)\b\s+',
]

# Numeric resolution markers the keyword QUALITY_PATTERNS miss: 720p, 1080p/i, 2160p,
# 3840P, 480p, etc. - a 3-4 digit run glued directly to p/i. The 3-digit lower bound
# excludes 2-digit noise; the 4-digit upper bound excludes 5-digit numbers (10800p won't
# match). The p/i must be GLUED to the digits (no space): real markers are always written
# "720P"/"3840P", and requiring the glue avoids stripping a spaced standalone P/I such as a
# roman numeral ("Volume 100 I"). The p/i \b anchor keeps bare numbers (1080, "Channel 4")
# intact. Applied with re.IGNORECASE in the ignore_quality block, like QUALITY_PATTERNS.
RESOLUTION_PATTERNS = [
    r'\b\d{3,4}[pi]\b',
]

REGIONAL_PATTERNS = [
    # East/West are NOT stripped here - they distinguish separate channel feeds
    # ("HBO East" vs "HBO West"); normalize_name strips bare East/West separately,
    # gated on ignore_regional.
    # bug-066: bare " Pacific"/" Central"/" Mountain"/" Atlantic" are brand tokens far
    # more often than feed markers ("Comedy Central", "The Atlantic"), so they are
    # stripped ONLY in their parenthesized form, never as bare words.
    r'\s*\([Pp][Aa][Cc][Ii][Ff][Ii][Cc]\)\s*',
    r'\s*\([Cc][Ee][Nn][Tt][Rr][Aa][Ll]\)\s*',
    r'\s*\([Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]\)\s*',
    r'\s*\([Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]\)\s*',
]

# bug-066 (opt-in half): the BARE-word forms of the time-zone region markers. OFF by
# default - a plugin enables them by setting the class attr _STRIP_BARE_REGION = True
# (or passing strip_bare_region=True). Lineuparr opts in: it strips these for SCORING
# and relies on its post-match region filter - which reads the ORIGINAL un-normalized
# name - to enforce region correctness, so a "Sportsnet Pacific" stream can score-match
# a "Sportsnet (P)" lineup and an "HBO West" lineup keeps the "HBO Pacific" feed. Plugins
# WITHOUT that filter (Stream-Mapparr) leave it off, or brand tokens like "Comedy Central"
# / "The Atlantic" would be wrongly truncated.
REGIONAL_BARE_PATTERNS = [
    r'\s[Pp][Aa][Cc][Ii][Ff][Ii][Cc]',
    r'\s[Cc][Ee][Nn][Tt][Rr][Aa][Ll]',
    r'\s[Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]',
    r'\s[Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]',
]

# Country tokens for the delimited provider-prefix patterns below; curated so a
# bare delimited word ("(SPORTS)") isn't misread as a country.
_PREFIX_COUNTRY = r'US|USA|UK|CA|AU|FR|DE|ES|IT|NL|BR|MX|IN'

# (open, close) pairs; open and close must MATCH, so "(US]" / "│US)" are rejected.
_DELIM_PAIRS = ((r'\(', r'\)'), (r'\[', r'\]'), (r'\|', r'\|'), ('┃', '┃'), ('│', '│'))


def _balanced_delim(token):
    """Regex fragment matching `token` wrapped in one MATCHED delimiter pair:
    (token) [token] |token| ┃token┃ │token│. `token` may contain a capture group."""
    return '(?:' + '|'.join(
        o + r'\s*(?:' + token + r')\s*' + c for o, c in _DELIM_PAIRS
    ) + ')'


# Strip a leading box-bar bouquet/source tag with arbitrary inner text
# ("┃CANAL+┃ NPO 1" -> "NPO 1"); box bars never occur in real names, so this is
# always safe and also covers leading "┃XX┃" country tags.
_LEADING_BAR_TAG_RE = re.compile(r'^\s*[┃│]\s*[^┃│]*[┃│]\s*')

GEOGRAPHIC_PATTERNS = [
    r'\b[A-Z]{2,3}[:┃│]\s*',
    r'\b[A-Z]{2,3}\s*-\s*',
    # Matched bar pair only ("|US|", "┃US┃") - a mismatched "|US┃" is noise.
    r'(?:\|[A-Z]{2,3}\||┃[A-Z]{2,3}┃|│[A-Z]{2,3}│)\s*',
    r'\[[A-Z]{2,3}\]\s*',
]

# Enhanced provider prefix patterns for IPTV-specific naming
PROVIDER_PREFIX_PATTERNS = [
    r'^(?:' + _PREFIX_COUNTRY + r')\s*[:\-\|┃│]\s*',
    # Bare country tag + whitespace, no separator (e.g. "US Racer Network",
    # "FR beIN SPORTS MAX", "MEX Bein Sports"). Restricted to a curated set so
    # it cannot eat a real channel name: "USA Network" (USA != US + space),
    # "In Country Television" ("IN") and "IT Crowd" ("IT") are all safe too.
    r'^(?:US|UK|CA|AU|FR|DE|MX|MEX|FRA|GER)\s+',
    # "USA " space prefix as a US country tag ("USA  ABC", "USA BET"). A
    # negative lookahead for NETWORK protects the real channel "USA Network"
    # (these feeds tag that one as "US ..."/"US: ...", never bare "USA ").
    r'^USA\s+(?!NETWORK\b)',
    # Country code glued directly to a quality tag with no separator
    # ("UKSD: Sky Sports", "UKHD ESPN", "USFHD ...").
    r'^(?:US|UK)(?:SD|HD|FHD|UHD|FD|HEVC|4K|8K)\b\s*[:\-\|┃│]?\s*',
    # Bracketed/piped country tag with a MATCHED delimiter pair ("(US)", "│US│").
    r'^\s*' + _balanced_delim(_PREFIX_COUNTRY) + r'\s*',
    r'\s*[\|┃│]\s*(?:' + _PREFIX_COUNTRY + r')\s*$',
    # Content-category group prefixes used by some IPTV providers.
    r'^(?:ADULT|EROTIC|PRIME|GOLD)\s*[:\-\|┃│]\s*',
    # FAST streaming-platform source tags (Roku, Tubi, Pluto, etc.). These mark
    # the distribution platform, not the channel or its country, so strip them
    # for matching ("RK: beIN Sports Xtra" -> "beIN Sports Xtra"). A separator
    # is required so this can't eat real names like "GOLF" or "PLEX TV Movies".
    r'^(?:RK|GO|TUBI|PLUTO|XUMO|PLEX|STIRR|FREEVEE|GLANCE)\s*[:\-\|┃│]\s*',
]

MISC_PATTERNS = [
    r'\s*\([^)]*\)\s*',
]

# --------------------------------------------------------------------------- #
# Stylized-Unicode decoration stripping
# --------------------------------------------------------------------------- #
# Streams tag names with stylized-Unicode tier/format markers (superscript
# "WEATHERNATION RAW", small-cap "FHD", bullet-prefixed "CNN") that the ASCII tag
# regexes below cannot see. We drop whole tokens that are pure decoration BEFORE
# the ASCII pipeline runs. Detection is by Unicode character *name* (not code-point
# ranges), so it covers superscripts, "modifier letter" superscript capitals, and
# Latin small-caps wherever they live (e.g. small-cap H is U+029C in IPA Extensions
# and modifier V is U+2C7D in Latin-Ext-C, both outside the obvious blocks).

# Ornament glyphs whose Unicode name carries no decoration keyword.
_DECORATIVE_SYMBOLS = frozenset("◉")  # FISHEYE; add individual chars (not strings) here


def _is_decorative_char(ch):
    """True for a stylized letterform/ornament that carries no semantic content in a
    channel name (superscripts, subscripts, modifier-letter superscript capitals,
    Latin small-capitals, curated bullets). ASCII and ordinary letters return False."""
    if ch.isascii():
        return False
    if ch in _DECORATIVE_SYMBOLS:
        return True
    try:
        nm = unicodedata.name(ch)
    except ValueError:
        # unnamed code point (control char / lone surrogate) -> not decoration
        return False
    return ('SUPERSCRIPT' in nm or 'SUBSCRIPT' in nm
            or 'SMALL CAPITAL' in nm or 'MODIFIER LETTER' in nm)


def _strip_stylized_tokens(name):
    """Drop whitespace tokens that are pure stylized decoration, then NFKD-canonicalize
    the remainder. A token is decoration when it has >=1 decorative char, no ASCII
    alphanumeric, and every char is decorative or ASCII punctuation (so a bullet glued
    to a colon, or "HD/RAW" written in superscripts, are dropped too). Real ASCII words
    (Gold/VIP) and non-Latin letters (Arabic/Cyrillic/CJK) are always kept. ASCII-only
    input is returned unchanged via the fast path (no per-char work; NFKD is a no-op
    on ASCII, so skipping it changes nothing)."""
    if name.isascii():
        return name
    kept = []
    for tok in name.split():
        has_decorative = any(_is_decorative_char(c) for c in tok)
        has_ascii_alnum = any(c.isascii() and c.isalnum() for c in tok)
        only_decorative_or_punct = all(
            _is_decorative_char(c) or (c.isascii() and not c.isalnum()) for c in tok
        )
        if has_decorative and only_decorative_or_punct and not has_ascii_alnum:
            continue  # pure decoration -> drop the whole token
        kept.append(tok)
    return unicodedata.normalize('NFKD', ' '.join(kept))


# --------------------------------------------------------------------------- #
# Emoji-as-letter + emoji decoration normalization
# --------------------------------------------------------------------------- #
# Some streams use an emoji AS A LETTER inside a word: "SP⚽RTS" / "Sp⚽rts" where the
# soccer ball stands in for 'o' (= SPORTS, the beIN family). _strip_stylized_tokens keeps
# the token (it has ASCII alnum) and process_string_for_matching would turn the ball into a
# space ("sp rts"), so it never matches "sports". We substitute the glyph for the letter it
# replaces (only when flanked by ASCII letters) and strip emoji used purely as decoration.

# Emoji that visually replace an ASCII letter when embedded in a word. Extensible.
_EMOJI_LETTER_MAP = {'⚽': 'o'}            # SOCCER BALL = 'o'  (SP⚽RTS -> SPORTS)
# Pictographic ornaments to delete. NOTE: ⚽ is intentionally in BOTH maps - the letter
# map handles it mid-word (-> 'o'); here it catches any ⚽ NOT flanked by ASCII letters
# (standalone/edge), which the substitution above leaves untouched.
_EMOJI_ORNAMENTS = frozenset('♬☾⚽')       # beamed notes, last-quarter moon, soccer ball
# Zero-width / invisible code points that only add noise to a name.
_ZERO_WIDTH = ('️', '‍')         # VARIATION SELECTOR-16, ZERO WIDTH JOINER


def _normalize_emoji(name):
    """Map emoji-as-letters to their letter and strip emoji decoration.

    The letter substitution fires ONLY when the glyph is flanked by ASCII letters
    (so "SP⚽RTS" -> "SPoRTS" but a standalone/edge "⚽" is treated as decoration and
    dropped). Zero-width selectors and ornament pictographs are deleted outright.
    ASCII-only input is returned unchanged (no emoji possible)."""
    if name.isascii():
        return name
    for zw in _ZERO_WIDTH:
        if zw in name:
            name = name.replace(zw, '')
    for glyph, letter in _EMOJI_LETTER_MAP.items():
        if glyph in name:
            name = re.sub(r'(?<=[A-Za-z])' + re.escape(glyph) + r'(?=[A-Za-z])', letter, name)
    if any(c in _EMOJI_ORNAMENTS for c in name):
        name = ''.join(c for c in name if c not in _EMOJI_ORNAMENTS)
    return name


class FuzzyMatcherCore:
    """Pure, stateless matching primitives shared across the Dispatcharr plugins.

    Subclass this and add the plugin-specific orchestration layer (DB load, matching
    entry points, and one feature block). The core references self only for self.logger
    and the per-instance caches; it never reads plugin_dir / a channel DB / alias maps.
    """

    def __init__(self, match_threshold=80, logger=None):
        self.match_threshold = match_threshold
        self.logger = logger or LOGGER
        # Cache for pre-normalized stream names (performance optimization). The core
        # initializes the storage; the cache-filling glue (precompute_normalizations /
        # _get_cached_*) is plugin-side because its flag handling is plugin-specific.
        self._norm_cache = {}  # raw_name -> normalized_lower
        self._norm_nospace_cache = {}  # raw_name -> normalized_nospace
        self._processed_cache = {}  # raw_name -> processed_for_matching
        # Cache for callsign extraction: raw_name -> (callsign|None, is_high_conf)
        self._callsign_cache = {}
        # Optional set of known-real callsigns a plugin supplies from its channel DB so
        # the ladder can rescue a denylisted-but-real station (KING/WAVE/WOOD/WHO). None
        # = no rescue, identical to the pure Lineuparr ladder. Assign it via
        # set_known_callsigns() so the callsign cache is cleared when it changes.
        self._known_callsigns = None

    def set_known_callsigns(self, known):
        """Supply the plugin's known-real callsign set (or None to disable rescue).

        Clears the callsign cache so a changed set takes effect on the next extraction.
        """
        self._known_callsigns = frozenset(known) if known else None
        self._callsign_cache.clear()

    def normalize_name(self, name, user_ignored_tags=None, ignore_quality=True, ignore_regional=True,
                       ignore_geographic=True, ignore_misc=True, remove_cinemax=False,
                       remove_country_prefix=False, strip_bare_region=None):
        """
        Normalize channel or stream name for matching by removing tags, prefixes, and noise.

        remove_cinemax / remove_country_prefix are optional opt-in superset behaviors (a
        plugin sets them True); both default False, so the default path is unchanged.

        strip_bare_region opts into stripping BARE time-zone region words (Pacific/Central/
        Mountain/Atlantic) for scoring - see REGIONAL_BARE_PATTERNS. None (the default)
        resolves it from the subclass class attr _STRIP_BARE_REGION (default False), so a
        plugin enables it once via that attr instead of threading it through every call.
        """
        if user_ignored_tags is None:
            user_ignored_tags = []
        if strip_bare_region is None:
            strip_bare_region = getattr(self, "_STRIP_BARE_REGION", False)

        original_name = name

        name = _LEADING_BAR_TAG_RE.sub('', name)  # leading "┃CANAL+┃" bouquet tag

        # Map emoji-as-letters (⚽ = 'o' in "SP⚽RTS") and strip emoji decoration, before
        # the stylized-Unicode strip and ASCII regexes below - so "beIN SP⚽RTS" -> "beIN sports".
        name = _normalize_emoji(name)

        # Strip stylized-Unicode decoration (superscript/small-cap tier markers,
        # bullets) up front so the ASCII tag regexes below see plain text. Runs
        # unconditionally: a token written in superscript/small-caps is decoration
        # regardless of tag_handling, and it would otherwise block matches
        # (e.g. a superscript-RAW suffix never matches channel "WeatherNation").
        name = _strip_stylized_tokens(name)

        # Strip IPTV provider prefixes BEFORE hyphen normalization so that
        # "FR - Canal+ FHD" loses its "FR - " while the hyphen is still a
        # hyphen. After hyphen normalization the separator would become a space
        # and the pattern would fail to match, leaving "FR" as a stray token.
        for pattern in PROVIDER_PREFIX_PATTERNS:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Quality patterns (before space normalization). Loop until stable so
        # chained suffixes like "4K HDR" or "UHD HDR" are fully stripped in
        # successive passes (each pass may expose a token for the next).
        if ignore_quality:
            prev = None
            while prev != name:
                prev = name
                # Strip numeric resolution markers (3840P/2160P/1080P/720P/...) before the
                # digit/letter spacer below would split "3840P" into "3840 P".
                # Must run before QUALITY_PATTERNS so that removing " 4K " does not glue
                # "SPoRTS" to "3840P" and break the word-boundary anchor.
                for pattern in RESOLUTION_PATTERNS:
                    name = re.sub(pattern, '', name, flags=re.IGNORECASE)
                for pattern in QUALITY_PATTERNS:
                    name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Normalize spacing around numbers
        name = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', name)
        name = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', name)

        # Normalize hyphens to spaces
        name = re.sub(r'-', ' ', name)

        # Replace dots between word chars with spaces (e.g. "JusticeCentral.TV"
        # → "JusticeCentral TV"). Keeps the dot-suffix variant equivalent to
        # the spaced form for matching purposes.
        name = re.sub(r'(?<=\w)\.(?=\w)', ' ', name)

        # Normalize number-words to digits so "BBC Three" and "BBC 3" share
        # tokens. Critical for cases like "Three Angels Broadcasting Network"
        # vs "3 Angels Broadcasting Network", and for BBC One/Two/Three/Four
        # vs BBC 1/2/3/4. Bounded by word boundaries so brand names with
        # embedded letters (e.g. "Onesimus") aren't corrupted.
        _NUM_WORDS = {
            "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
            "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
            "eleven": "11", "twelve": "12",
        }
        def _num_repl(m):
            return _NUM_WORDS[m.group(0).lower()]
        name = re.sub(
            r'\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b',
            _num_repl, name, flags=re.IGNORECASE,
        )

        # Split CamelCase boundaries so "JusticeCentral" becomes "Justice
        # Central" and "DangerTV" becomes "Danger TV". Two separate patterns:
        #   1. lower → Upper followed by lower  (Justice|Central, Danger|Iq → no, etc.)
        #   2. lower(4+) → UPPER acronym at word boundary  (Danger|TV, Beauty|IQ)
        # The 4-char floor on rule 2 protects short brand names like "MeTV" and
        # "truTV" whose existing EPG matches rely on the un-split form.
        name = re.sub(r'([a-z])([A-Z][a-z])', r'\1 \2', name)
        name = re.sub(r'([a-z]{4,})([A-Z]{2,})\b', r'\1 \2', name)

        # Strip region tokens (East / Eastern / West and the (E)/(W)
        # abbreviations) for SCORING. Region CORRECTNESS - East vs West vs
        # Pacific - is enforced separately by the post-match region filter in
        # match_all_streams, which reads the ORIGINAL un-normalized names, not
        # this output. Stripping here lets a regionless lineup channel
        # ("Food Network") still score-match a region-tagged stream
        # ("Food Network Eastern" / "... East") instead of being dragged below
        # threshold by the extra token; the filter then accepts it (regionless
        # defaults to East). "Western"/"Westerns" is a movie genre, NOT a
        # region, so bare "west" is stripped only on a word boundary (\bwest\b
        # does not match inside "western") and "western" is never touched.
        if ignore_regional:
            name = re.sub(r'\(\s*(?:eastern|east|e|west|w)\s*\)', ' ', name, flags=re.IGNORECASE)
            name = re.sub(r'\b(?:eastern|east|west)\b', ' ', name, flags=re.IGNORECASE)

        # Remove leading parenthetical prefixes
        while name.lstrip().startswith('('):
            new_name = re.sub(r'^\s*\([^\)]+\)\s*', '', name)
            if new_name == name:
                break
            name = new_name

        # Opt-in: remove a country-code prefix (multi-country DBs). Strips a 2-3
        # letter colon/space prefix unless it is a quality tag. Off by default;
        # PROVIDER_PREFIX_PATTERNS above already removes the curated country
        # prefixes, so this only catches the remainder. (Stream-Mapparr opts in.)
        if remove_country_prefix:
            quality_tags = {'HD', 'SD', 'FD', 'UHD', 'FHD'}
            prefix_match = re.match(r'^([A-Z]{2,3})[:\s]\s*', name)
            if prefix_match:
                prefix = prefix_match.group(1).upper()
                if prefix not in quality_tags:
                    name = name[len(prefix_match.group(0)):]

        # Opt-in: remove a leading "Cinemax" (for channels containing "max"). Off
        # by default. (Stream-Mapparr opts in.)
        if remove_cinemax:
            name = re.sub(r'\bCinemax\b\s*', '', name, flags=re.IGNORECASE)

        # Build pattern list based on flags
        patterns_to_apply = []
        if ignore_regional:
            patterns_to_apply.extend(REGIONAL_PATTERNS)
            if strip_bare_region:
                patterns_to_apply.extend(REGIONAL_BARE_PATTERNS)
        if ignore_geographic:
            patterns_to_apply.extend(GEOGRAPHIC_PATTERNS)
        if ignore_misc and ignore_regional:
            patterns_to_apply.extend(MISC_PATTERNS)

        for pattern in patterns_to_apply:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Apply user-configured ignored tags
        for tag in user_ignored_tags:
            escaped_tag = re.escape(tag)
            if '[' in tag or ']' in tag or '(' in tag or ')' in tag:
                name = re.sub(escaped_tag + r'\s*', '', name, flags=re.IGNORECASE)
            else:
                if re.match(r'^\w+$', tag):
                    name = re.sub(r'\b' + escaped_tag + r'\b', '', name, flags=re.IGNORECASE)
                else:
                    name = re.sub(escaped_tag + r'\s*', '', name, flags=re.IGNORECASE)

        # Remove callsigns in parentheses
        if ignore_regional:
            name = re.sub(r'\([KW][A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)
        else:
            name = re.sub(r'\([KW](?!EST\)|ACIFIC\)|ENTRAL\)|OUNTAIN\)|TLANTIC\))[A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)

        if ignore_regional:
            name = re.sub(r'\([A-Z0-9]+\)', '', name)

        # Remove common suffixes/prefixes
        name = re.sub(r'^The\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Network\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Channel\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+TV\s*$', '', name, flags=re.IGNORECASE)

        # Strip decorative Unicode markers (◉, ², superscript letters ᴿᴬᵂ…)
        # that some IPTV providers append as quality/status badges. Only
        # characters in decorator categories are removed; accented letters
        # (é, î, ü…) are in Ll/Lu and are preserved.
        name = ''.join(
            ' ' if unicodedata.category(c) in _DECORATOR_CATS else c
            for c in name
        )

        # Clean up whitespace
        name = re.sub(r'\s+', ' ', name).strip()

        if not name:
            self.logger.debug(f"normalize_name returned empty for: '{original_name}'")

        return name

    def calculate_similarity(self, str1, str2, min_ratio=0.0):
        """Levenshtein similarity ratio (0.0-1.0), defined as 1 - distance/max(len).
        If min_ratio > 0, returns 0.0 early when the result can't reach it.

        bug-026: the ratio MUST be distance/max(len), matching rapidfuzz
        Levenshtein.normalized_similarity. The old (len1+len2-distance)/(len1+len2)
        formula scored higher for the same edit distance and let numbered siblings
        ("Fox Sports 1" vs "2") pass threshold 95. The rapidfuzz fast path and the
        pure-Python fallback below compute the identical value.
        """
        if len(str1) == 0 or len(str2) == 0:
            return 0.0

        # Fast path: C-accelerated rapidfuzz when available (same definition).
        if _USE_RAPIDFUZZ:
            # Compute the true similarity, then apply the SAME gate as the pure-Python
            # early-exit below: a score that cannot reach min_ratio is 0.0 on both paths
            # (raw agreement below threshold), but a score landing EXACTLY on min_ratio is
            # KEPT (>=, inclusive). We deliberately do NOT use rapidfuzz's own score_cutoff:
            # it treats the cutoff as strict-greater and quantizes it onto the achievable
            # distance grid, so a true 0.8 against a 0.8 cutoff is spuriously zeroed while the
            # pure-Python path returns it. With min_ratio=0.0 the gate is a no-op.
            sim = _rf_lev.normalized_similarity(str1, str2)
            return sim if (min_ratio <= 0.0 or sim >= min_ratio) else 0.0

        if len(str1) < len(str2):
            str1, str2 = str2, str1
        len1, len2 = len(str1), len(str2)
        max_len = len1  # the longer string after the swap

        # Length-difference pre-check: minimum possible distance is (len1 - len2),
        # so the max possible ratio is (max_len - (len1 - len2)) / max_len.
        if min_ratio > 0:
            max_possible = (max_len - (len1 - len2)) / max_len
            if max_possible < min_ratio:
                return 0.0

        previous_row = list(range(len2 + 1))
        for i, c1 in enumerate(str1):
            current_row = [i + 1]
            for j, c2 in enumerate(str2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            # Early termination: a lower bound on the final distance is the current
            # row minimum minus the str1 chars still unprocessed.
            if min_ratio > 0:
                min_distance_so_far = min(current_row)
                remaining = len1 - i - 1
                best_possible_distance = max(0, min_distance_so_far - remaining)
                best_possible_ratio = (max_len - best_possible_distance) / max_len
                if best_possible_ratio < min_ratio:
                    return 0.0
            previous_row = current_row

        distance = previous_row[-1]
        return (max_len - distance) / max_len

    @staticmethod
    def _length_scaled_threshold(base_threshold, shorter_len):
        """Require higher similarity for shorter strings to avoid false positives."""
        if shorter_len <= 4:
            return max(base_threshold, 95)
        elif shorter_len <= 8:
            return max(base_threshold, 90)
        return base_threshold

    @staticmethod
    def _has_token_overlap(str_a, str_b, min_token_len=4, require_majority=False):
        """Check that distinctive tokens are shared between two strings.

        Basic mode: at least one token (>= min_token_len) must be shared.
        Majority mode: uses all tokens (>= 2 chars) and requires that more than
        half of the smaller set overlaps. Catches false positives like
        "america racing" vs "america bbc" while allowing single-token matches.
        """
        # "network"/"channel"/"television" are generic brand-suffix words, not
        # distinctive - treat as common so the subset guard does not reject
        # cases like "FanDuel Sports Cincinnati" vs "FanDuel Sports Network
        # Cincinnati". (They're already stripped from end-of-string by
        # normalize_name; this catches mid-string occurrences.)
        common_words = {
            "the", "and", "of", "in", "on", "at", "to", "for", "a", "an",
            "network", "channel", "television",
        }

        if require_majority:
            # Use all meaningful tokens (>= 2 chars) for stricter checking.
            # Single-digit tokens (1, 2, 3, ...) are kept because they're
            # channel-distinguishing (BBC 1 vs BBC 2, ESPN 1 vs ESPN 2 etc.)
            # even though they're only 1 char.
            def _meaningful(t):
                if t in common_words:
                    return False
                return len(t) >= 2 or t.isdigit()
            tokens_a = {t for t in str_a.split() if _meaningful(t)}
            tokens_b = {t for t in str_b.split() if _meaningful(t)}
            if not tokens_a or not tokens_b:
                return True
            shared = tokens_a & tokens_b
            if not shared:
                return False
            smaller = min(len(tokens_a), len(tokens_b))
            if not len(shared) > smaller / 2:
                return False

            unique_a = tokens_a - tokens_b
            unique_b = tokens_b - tokens_a

            # Subset guard: when one side is a strict subset of the other and
            # the larger side has a distinctive token the smaller lacks, the
            # candidate is a more specific channel than the query and the high
            # fuzzy score is a false positive. Catches e.g. "Nickelodeon" vs
            # "Nickelodeon Teen". Threshold is >=4 chars; "live"/"now" are
            # explicitly non-distinctive (stream label variants) so "ABC News"
            # still matches "ABC News Live". Pure-digit tokens >=2 chars
            # (e.g. "360") are also distinctive: "Canal+Sport" must not match
            # "Canal+Sport 360".
            if not unique_a:
                if any(_is_distinctive(t) for t in unique_b):
                    return False
            elif not unique_b:
                if any(_is_distinctive(t) for t in unique_a):
                    return False

            # Divergent guard: when BOTH sides have unique tokens AND at least
            # one of those unique tokens is a distinctive (>=4 char) word, the
            # strings describe different brands and the fuzzy score is
            # misleading. Catches "Sky Cinema Disney" vs "Sky Cinema Decades"
            # (decades = 7 chars), "Sky Cinema Fast" vs "Sky Cinema Family",
            # and "BBC One vs BBC Two" once number-words become digits earlier.
            # Number-word→digit normalization (in normalize_name) reduces this
            # guard's risk: "Three Angels" / "3 Angels" both become "3 angels"
            # before reaching here, so they never trigger this case.
            if unique_a and unique_b:
                if any(len(t) >= 4 for t in unique_a | unique_b):
                    return False

            # Numeric/ordinal divergent guard: when BOTH sides have a numeric
            # or ordinal token unique to them, they are sibling channels
            # distinguished by number (BBC One vs BBC Two; ESPN 1 vs ESPN 2).
            # Short tokens like "one"/"two" wouldn't trip the divergent guard
            # above so they need their own check.
            _NUMERIC = {
                "one", "two", "three", "four", "five", "six", "seven", "eight",
                "nine", "ten", "eleven", "twelve",
                "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12",
                "first", "second", "third", "fourth", "fifth",
            }
            if (unique_a & _NUMERIC) and (unique_b & _NUMERIC):
                return False

            return True

        # Basic mode: at least one long token shared
        tokens_a = {t for t in str_a.split() if t not in common_words and len(t) >= min_token_len}
        tokens_b = {t for t in str_b.split() if t not in common_words and len(t) >= min_token_len}
        if not tokens_a or not tokens_b:
            return True
        return bool(tokens_a & tokens_b)

    def process_string_for_matching(self, s):
        """Normalize for token-sort matching: NFKD-fold (so "ＨＢＯ"->"hbo", "²"->"2",
        "ﬁ"->"fi", accents drop), lowercase, keep alphanumerics of any script
        (isalnum keeps Cyrillic/CJK/Arabic rather than erasing them) plus '+'
        (Disney+/Discovery+/Paramount+ stay distinct), sort tokens."""
        s = unicodedata.normalize('NFKD', s)
        s = ''.join(char for char in s if unicodedata.category(char) != 'Mn')
        s = s.lower()
        s = re.sub(r'([^\W\d_])(\d)', r'\1 \2', s)  # split letter-glued digit, any script
        cleaned_s = ""
        for char in s:
            if char.isalnum() or char == '+':
                cleaned_s += char
            else:
                cleaned_s += ' '
        tokens = sorted([token for token in cleaned_s.split() if token])
        return " ".join(tokens)

    @staticmethod
    def _trailing_number(name):
        """Return the integer value of a space-separated, purely-numeric
        trailing token, or None. 'HBO 2' -> 2, 'DIRECTV 4K Live 1' -> 1,
        'ESPN' -> None, 'ESPN2' -> None (digit not space-separated). Used to
        reject 'Foo 1' vs 'Foo 2' false positives -- different channels that
        otherwise fuzzy-match almost perfectly."""
        m = re.search(r'(?:^|\s)(\d{1,4})\s*$', name or "")
        return int(m.group(1)) if m else None

    # Words that match the callsign regex shape but are never US broadcast
    # callsigns. A US callsign (K/W + 2-4 letters) is shape-identical to many
    # common English words, so the loose Priority-4 pattern mis-extracts them -
    # e.g. "with" in "Bizarre Foods with Andrew Zimmern" becomes callsign
    # "WITH". Regex alone cannot tell "WITH" from "WABC"; frequent K/W-initial
    # words are denied explicitly so they never extract as a callsign. WWE/WWF/
    # WCW stop wrestling show names extracting as false-positive callsigns.
    _CALLSIGN_DENYLIST = frozenset({
        'WWE', 'WWF', 'WCW', 'EAST',
        'WAR', 'WARS', 'WARM', 'WASH', 'WATCH', 'WAVE', 'WAVES', 'WAY', 'WAYS',
        'WEB', 'WEEK', 'WELL', 'WENT', 'WERE', 'WEST', 'WHAT', 'WHEN', 'WHERE',
        'WHICH', 'WHILE', 'WHITE', 'WHO', 'WHY', 'WIDE', 'WIFE', 'WILD', 'WILL',
        'WIND', 'WINE', 'WING', 'WINGS', 'WINS', 'WIRE', 'WISE', 'WISH', 'WITH',
        'WOLF', 'WOMAN', 'WOMEN', 'WOOD', 'WORD', 'WORDS', 'WORK', 'WORKS',
        'WORLD', 'WORM', 'WORN', 'WRAP',
        'KEEN', 'KEEP', 'KEPT', 'KEY', 'KEYS', 'KICK', 'KID', 'KIDS', 'KILL',
        'KIND', 'KING', 'KINGS', 'KISS', 'KITE', 'KNEE', 'KNEW', 'KNOW', 'KNOWN',
    })

    def _is_callsign_allowed(self, callsign):
        """A candidate callsign is allowed if it is not denylisted, OR the plugin
        supplied a known-real callsign set that contains it (DB rescue)."""
        return (callsign not in self._CALLSIGN_DENYLIST
                or (self._known_callsigns is not None and callsign in self._known_callsigns))

    def _compute_callsign_with_confidence(self, channel_name):
        """
        Extract US TV callsign with a confidence flag.

        Returns (callsign, is_high_confidence). High confidence =
        Priorities 1-3 (parenthesized / suffixed-paren / end-of-name).
        Priority 4 (any loose word) is low confidence. (None, False)
        when nothing extractable.

        A denylisted word is rejected UNLESS the plugin supplied a known-real
        callsign set (set_known_callsigns) that contains it - that is the DB
        rescue for real stations whose callsign collides with a common word
        (KING/WAVE/WOOD/WHO). With no set supplied this is the pure ladder.
        """
        # Remove common provider prefixes
        channel_name = re.sub(r'^D\d+-', '', channel_name)
        channel_name = re.sub(r'^USA?\s*[^a-zA-Z0-9]*\s*', '', channel_name, flags=re.IGNORECASE)

        # Priority 1: Callsigns in parentheses (most reliable)
        paren_match = re.search(r'\(([KW][A-Z]{3})(?:-[A-Z\s]+)?\)', channel_name, re.IGNORECASE)
        if paren_match:
            callsign = paren_match.group(1).upper()
            if self._is_callsign_allowed(callsign):
                return callsign, True

        # Priority 1b: grandfathered 3-letter callsigns in parentheses without a suffix
        # (WWL/WJZ/KYW/WRC). Suffixed forms fall through to Priority 2. bug-062.
        paren3_match = re.search(r'\(([KW][A-Z]{2})\)', channel_name, re.IGNORECASE)
        if paren3_match:
            callsign = paren3_match.group(1).upper()
            if self._is_callsign_allowed(callsign):
                return callsign, True

        # Priority 2: Callsigns with suffix in parentheses
        paren_suffix_match = re.search(r'\(([KW][A-Z]{2,4}-(?:TV|CD|LP|DT|LD))\)', channel_name, re.IGNORECASE)
        if paren_suffix_match:
            return paren_suffix_match.group(1).upper(), True

        # Priority 3: Callsigns at the end
        end_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\s*(?:\.[a-z]+)?\s*$', channel_name, re.IGNORECASE)
        if end_match:
            callsign = end_match.group(1).upper()
            if self._is_callsign_allowed(callsign):
                return callsign, True

        # Priority 4: Any word matching callsign pattern (low confidence)
        word_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\b', channel_name, re.IGNORECASE)
        if word_match:
            callsign = word_match.group(1).upper()
            if self._is_callsign_allowed(callsign):
                return callsign, False

        return None, False

    def _extract_callsign_with_confidence(self, channel_name):
        """
        Cached wrapper around _compute_callsign_with_confidence.

        Extraction is pure in channel_name (and in self._known_callsigns, which is
        held fixed between set_known_callsigns calls), so results are memoized - the
        anchor calls this once per stream over a fixed candidate list, which is
        otherwise massively redundant across the per-channel matching loop.
        """
        cached = self._callsign_cache.get(channel_name)
        if cached is not None:
            return cached
        result = self._compute_callsign_with_confidence(channel_name)
        self._callsign_cache[channel_name] = result
        return result

    def extract_callsign(self, channel_name):
        """
        Extract US TV callsign from channel name with priority order.
        Returns None if common false positives appear alone.
        """
        callsign, _ = self._extract_callsign_with_confidence(channel_name)
        return callsign

    def normalize_callsign(self, callsign):
        """Remove the broadcast suffix (-TV/-CD/-LP/-DT/-LD) from a callsign."""
        if callsign:
            callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
        return callsign
