"""
Fuzzy Matcher Module for Dispatcharr Plugins
Handles fuzzy matching, normalization, and channel database loading.
Reusable across multiple plugins (Stream-Mapparr, Channel Mapparr, etc.)
"""

import os
import re
import json
import logging
import unicodedata
from glob import glob

# The pure matching primitives (normalize_name, calculate_similarity,
# process_string_for_matching, the callsign ladder, the regex tables, ...) live in the
# vendored shared core. This plugin subclasses it (class FuzzyMatcher below) and keeps
# only its plugin-specific layer. See MATCHER-STANDARDIZATION-PLAN.md.
try:  # plugin package context (production)
    from .matching_core import (
        FuzzyMatcherCore,
        _is_decorative_char,  # noqa: F401  re-exported for the decoration unit tests
        _normalize_emoji,  # noqa: F401
        _strip_stylized_tokens,  # noqa: F401
    )
except ImportError:  # standalone load (tests / direct import)
    from matching_core import (
        FuzzyMatcherCore,
        _is_decorative_char,  # noqa: F401
        _normalize_emoji,  # noqa: F401
        _strip_stylized_tokens,  # noqa: F401
    )

# Version: YY.DDD.HHMM (Julian date format: Year.DayOfYear.Time)
__version__ = "26.165.0009"

# Setup logging
LOGGER = logging.getLogger("plugins.fuzzy_matcher")


# Canonical feed-zone detection (East/West) for zone-aware stream routing.
# Matches parenthesized (W)/(E)/(WEST)/(EAST) or the bare words WEST/EAST. Bare
# single letters W/E are intentionally NOT matched (e.g. the UK channel "W",
# "E! Entertainment") and "EAST"/"WEST" embedded in a larger word ("EastEnders")
# is excluded by the word boundaries.
_ZONE_WEST_RE = re.compile(r'\(\s*W(?:EST)?\s*\)|\bWEST\b', re.IGNORECASE)
_ZONE_EAST_RE = re.compile(r'\(\s*E(?:AST)?\s*\)|\bEAST\b', re.IGNORECASE)


class FuzzyMatcher(FuzzyMatcherCore):
    """Stream-Mapparr matcher: the shared pure core (FuzzyMatcherCore) plus this
    plugin's layer — channel/broadcast DB loading, zone expansion, and the matching
    entry points (find_best_match / alias_lookup / fuzzy_match / OTA)."""
    
    def __init__(self, plugin_dir=None, match_threshold=85, logger=None):
        """
        Initialize the fuzzy matcher.

        Args:
            plugin_dir: Directory where the plugin and channel JSON files are located (optional)
            match_threshold: Minimum similarity score (0-100) for a match to be accepted
            logger: Logger instance (optional)
        """
        super().__init__(match_threshold=match_threshold, logger=logger)
        self.plugin_dir = plugin_dir or os.path.dirname(__file__)

        # Channel data storage
        self.broadcast_channels = []  # Channels with callsigns
        self.premium_channels = []  # Channel names only (for fuzzy matching)
        self.premium_channels_full = []  # Full channel objects with category
        self.channel_lookup = {}  # Callsign -> channel data mapping
        self.country_codes = None  # Track which country databases are currently loaded

        # Normalization cache for performance (avoids redundant normalize_name calls)
        self._norm_cache = {}          # raw_name -> normalized_lower
        self._norm_nospace_cache = {}   # raw_name -> normalized with spaces/&/- removed
        self._processed_cache = {}     # raw_name -> process_string_for_matching result
        self._cached_ignore_tags = None  # user_ignored_tags used during precompute
        self._cached_flags = {}        # ignore_quality/regional/geographic/misc used during precompute

        # Load all channel databases if plugin_dir is provided
        if self.plugin_dir:
            self._load_channel_databases()
    
    def _expand_zones(self, channel):
        """Expand a channel dict with a "zones" array into one dict per zone.

        A channel declaring `"zones": ["East", "West"]` yields one entry per
        zone with the zone suffix appended to `channel_name`. The base is NOT
        emitted — under `strip_all` tag handling the variants collapse to the
        same normalized key (so a zoneless stream still matches via fuzzy
        similarity), and under `keep_regional` the variants stay distinct.
        Emitting the base too would cause 3-way slot contention.

        Channels without a `zones` field yield themselves unchanged.
        """
        zones = channel.get('zones')
        if zones is None:
            yield channel
            return
        if not isinstance(zones, list):
            self.logger.warning(
                f"Malformed 'zones' field (expected list) on channel "
                f"{channel.get('channel_name')!r}: {zones!r} — treating as unzoned"
            )
            yield {k: v for k, v in channel.items() if k != 'zones'}
            return
        base = {k: v for k, v in channel.items() if k != 'zones'}
        base_name = base.get('channel_name', '').strip()
        if not zones:
            yield base
            return
        seen = set()
        for zone in zones:
            zone = str(zone).strip()
            if not zone or zone.lower() in seen:
                continue
            seen.add(zone.lower())
            variant = dict(base)
            variant['channel_name'] = f"{base_name} {zone}" if base_name else zone
            yield variant

    def _load_channel_databases(self):
        """Load all *_channels.json files from the plugin directory."""
        pattern = os.path.join(self.plugin_dir, "*_channels.json")
        channel_files = glob(pattern)
        
        if not channel_files:
            self.logger.warning(f"No *_channels.json files found in {self.plugin_dir}")
            return False
        
        self.logger.info(f"Found {len(channel_files)} channel database file(s): {[os.path.basename(f) for f in channel_files]}")
        
        total_broadcast = 0
        total_premium = 0
        
        for channel_file in channel_files:
            try:
                with open(channel_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extract the channels array from the JSON structure
                    channels_list = data.get('channels', []) if isinstance(data, dict) else data

                file_broadcast = 0
                file_premium = 0

                for raw_channel in channels_list:
                    channel_type = raw_channel.get('type', '').lower()

                    if 'broadcast' in channel_type or channel_type == 'broadcast (ota)':
                        # Broadcast channel with callsign (zones not applied to OTA)
                        self.broadcast_channels.append(raw_channel)
                        file_broadcast += 1

                        callsign = raw_channel.get('callsign', '').strip()
                        if callsign:
                            self.channel_lookup[callsign] = raw_channel
                            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                            if base_callsign != callsign:
                                self.channel_lookup[base_callsign] = raw_channel
                    else:
                        # Premium/cable/national channel — expand zones into variants
                        for channel in self._expand_zones(raw_channel):
                            channel_name = channel.get('channel_name', '').strip()
                            if channel_name:
                                self.premium_channels.append(channel_name)
                                self.premium_channels_full.append(channel)
                                file_premium += 1
                
                total_broadcast += file_broadcast
                total_premium += file_premium
                
                self.logger.info(f"Loaded from {os.path.basename(channel_file)}: {file_broadcast} broadcast, {file_premium} premium channels")
                
            except Exception as e:
                self.logger.error(f"Error loading {channel_file}: {e}")
        
        self.logger.info(f"Total channels loaded: {total_broadcast} broadcast, {total_premium} premium")
        self._load_broadcast_stations()
        # Feed the real, DB-known callsigns to the core's confidence ladder so a
        # denylisted-but-real station (KING/WAVE/WOOD/WHO) is rescued and still
        # extracts, while junk K/W words (WITH/WIND/...) stay denied.
        self.set_known_callsigns({k.upper() for k in self.channel_lookup})
        return True

    def _load_broadcast_stations(self):
        """Load the FCC station table (networks.json) into the OTA lookup.

        The per-country *_channels.json databases ship only premium
        (National/Regional) entries — no ``broadcast`` type, no ``callsign``
        field — so OTA/callsign matching relies on this US station table:
        callsign -> {network_affiliation, community_served_city,
        community_served_state, ...}. Each station is appended to
        ``broadcast_channels`` and indexed in ``channel_lookup`` by both its
        full callsign (``WEWS-TV``) and base callsign (``WEWS``) so a stream
        citing either form resolves. A missing file is non-fatal. Ported from
        Channel-Maparr to revive OTA matching after the US database lost its
        broadcast entries. bug-063.

        Returns the number of stations loaded.
        """
        stations_path = os.path.join(self.plugin_dir, "networks.json")
        if not os.path.exists(stations_path):
            self.logger.info("No networks.json present — OTA station table not loaded")
            return 0

        try:
            with open(stations_path, 'r', encoding='utf-8') as f:
                stations = json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading networks.json: {e}")
            return 0

        loaded = 0
        for station in stations:
            callsign = (station.get('callsign') or '').strip().upper()
            if not callsign:
                continue
            self.broadcast_channels.append(station)
            # setdefault: keep the first (primary) station for a given key so a
            # later subchannel entry can't clobber the main affiliate.
            self.channel_lookup.setdefault(callsign, station)
            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
            if base_callsign != callsign:
                self.channel_lookup.setdefault(base_callsign, station)
            loaded += 1

        self.logger.info(f"Loaded {loaded} OTA broadcast stations from networks.json")
        return loaded

    def reload_databases(self, country_codes=None):
        """
        Reload channel databases with specific country codes.

        Args:
            country_codes: List of country codes to load (e.g., ['US', 'UK', 'CA'])
                          If None, loads all available databases.

        Returns:
            bool: True if databases were loaded successfully, False otherwise
        """
        # Clear existing channel data
        self.broadcast_channels = []
        self.premium_channels = []
        self.premium_channels_full = []
        self.channel_lookup = {}

        # Update country_codes tracking
        self.country_codes = country_codes

        # Determine which files to load
        if country_codes:
            # Load only specified country databases
            channel_files = []
            for code in country_codes:
                file_path = os.path.join(self.plugin_dir, f"{code}_channels.json")
                if os.path.exists(file_path):
                    channel_files.append(file_path)
                else:
                    self.logger.warning(f"Channel database not found: {code}_channels.json")
        else:
            # Load all available databases
            pattern = os.path.join(self.plugin_dir, "*_channels.json")
            channel_files = glob(pattern)

        if not channel_files:
            self.logger.warning(f"No channel database files found to load")
            return False

        self.logger.info(f"Loading {len(channel_files)} channel database file(s): {[os.path.basename(f) for f in channel_files]}")

        total_broadcast = 0
        total_premium = 0

        for channel_file in channel_files:
            try:
                with open(channel_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Extract the channels array from the JSON structure
                    channels_list = data.get('channels', []) if isinstance(data, dict) else data

                file_broadcast = 0
                file_premium = 0

                for raw_channel in channels_list:
                    channel_type = raw_channel.get('type', '').lower()

                    if 'broadcast' in channel_type or channel_type == 'broadcast (ota)':
                        # Broadcast channel with callsign (zones not applied to OTA)
                        self.broadcast_channels.append(raw_channel)
                        file_broadcast += 1

                        callsign = raw_channel.get('callsign', '').strip()
                        if callsign:
                            self.channel_lookup[callsign] = raw_channel
                            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                            if base_callsign != callsign:
                                self.channel_lookup[base_callsign] = raw_channel
                    else:
                        # Premium/cable/national channel — expand zones into variants
                        for channel in self._expand_zones(raw_channel):
                            channel_name = channel.get('channel_name', '').strip()
                            if channel_name:
                                self.premium_channels.append(channel_name)
                                self.premium_channels_full.append(channel)
                                file_premium += 1

                total_broadcast += file_broadcast
                total_premium += file_premium

                self.logger.info(f"Loaded from {os.path.basename(channel_file)}: {file_broadcast} broadcast, {file_premium} premium channels")

            except Exception as e:
                self.logger.error(f"Error loading {channel_file}: {e}")

        self.logger.info(f"Total channels loaded: {total_broadcast} broadcast, {total_premium} premium")
        self._load_broadcast_stations()
        # Feed the real, DB-known callsigns to the core's confidence ladder so a
        # denylisted-but-real station (KING/WAVE/WOOD/WHO) is rescued and still
        # extracts, while junk K/W words (WITH/WIND/...) stay denied.
        self.set_known_callsigns({k.upper() for k in self.channel_lookup})
        return True

    
    
    def precompute_normalizations(self, names, user_ignored_tags=None,
                                   ignore_quality=True, ignore_regional=True,
                                   ignore_geographic=True, ignore_misc=True):
        """
        Pre-normalize a list of names and cache the results.
        Call this once before matching loops to avoid redundant normalization
        when matching many channels against the same stream list.
        Flags must match the flags passed to fuzzy_match() for correct results.
        """
        self._norm_cache.clear()
        self._norm_nospace_cache.clear()
        self._processed_cache.clear()
        self._cached_ignore_tags = user_ignored_tags
        self._cached_flags = {
            'ignore_quality': ignore_quality,
            'ignore_regional': ignore_regional,
            'ignore_geographic': ignore_geographic,
            'ignore_misc': ignore_misc,
        }

        for name in names:
            norm = self.normalize_name(name, user_ignored_tags,
                                       ignore_quality=ignore_quality,
                                       ignore_regional=ignore_regional,
                                       ignore_geographic=ignore_geographic,
                                       ignore_misc=ignore_misc)
            if norm and len(norm) >= 2:
                norm_lower = norm.lower()
                self._norm_cache[name] = norm_lower
                self._norm_nospace_cache[name] = re.sub(r'[\s&\-]+', '', norm_lower)
                self._processed_cache[name] = self.process_string_for_matching(norm)

        self.logger.info(f"Pre-normalized {len(self._norm_cache)} stream names (from {len(names)} total)")

    def _get_cached_norm(self, name, user_ignored_tags=None):
        """Get cached normalization or compute on the fly using stored flags."""
        if name in self._norm_cache:
            return self._norm_cache[name], self._norm_nospace_cache[name]
        tags = user_ignored_tags if user_ignored_tags is not None else self._cached_ignore_tags
        norm = self.normalize_name(name, tags, **self._cached_flags)
        if not norm or len(norm) < 2:
            return None, None
        norm_lower = norm.lower()
        return norm_lower, re.sub(r'[\s&\-]+', '', norm_lower)

    def _get_cached_processed(self, name, user_ignored_tags=None):
        """Get cached processed string or compute on the fly using stored flags."""
        if name in self._processed_cache:
            return self._processed_cache[name]
        tags = user_ignored_tags if user_ignored_tags is not None else self._cached_ignore_tags
        norm = self.normalize_name(name, tags, **self._cached_flags)
        if not norm or len(norm) < 2:
            return None
        return self.process_string_for_matching(norm)

    @staticmethod
    def extract_zone(name):
        """Canonical feed zone for zone-aware routing: 'WEST', 'EAST', or 'DEFAULT'.

        Recognizes parenthesized (W)/(E)/(WEST)/(EAST) and the bare words
        WEST/EAST. Bare single letters W/E (outside parentheses) are NOT zones —
        too many false positives ("W", "E! Entertainment"). 'DEFAULT' means
        unmarked (conventionally the East/primary feed in US lineups).
        """
        n = name or ''
        if _ZONE_WEST_RE.search(n):
            return 'WEST'
        if _ZONE_EAST_RE.search(n):
            return 'EAST'
        return 'DEFAULT'

    
    def extract_tags(self, name, user_ignored_tags=None):
        """
        Extract regional indicators, extra tags, and quality tags to preserve them.
        
        Returns:
            Tuple of (regional, extra_tags, quality_tags)
        """
        if user_ignored_tags is None:
            user_ignored_tags = []
        
        regional = None
        extra_tags = []
        quality_tags = []
        
        # Extract regional indicator
        regional_pattern_paren = r'\((East|West|Pacific|Central|Mountain|Atlantic)\)'
        regional_match = re.search(regional_pattern_paren, name, re.IGNORECASE)
        if regional_match:
            regional = regional_match.group(1).capitalize()
        else:
            regional_pattern_word = r'\b(East|West|Pacific|Central|Mountain|Atlantic)\b(?!.*\b(East|West|Pacific|Central|Mountain|Atlantic)\b)'
            regional_match = re.search(regional_pattern_word, name, re.IGNORECASE)
            if regional_match:
                regional = regional_match.group(1).capitalize()
        
        # Extract ALL tags in parentheses
        paren_tags = re.findall(r'\(([^\)]+)\)', name)
        first_paren_is_prefix = name.strip().startswith('(') if paren_tags else False
        
        for idx, tag in enumerate(paren_tags):
            # Skip first tag if it is a prefix
            if idx == 0 and first_paren_is_prefix:
                continue
            
            # Check if tag should be ignored
            if f"({tag})" in user_ignored_tags or f"[{tag}]" in user_ignored_tags:
                continue
            
            tag_upper = tag.upper()
            
            # Skip regional indicators
            if tag_upper in ['EAST', 'WEST', 'PACIFIC', 'CENTRAL', 'MOUNTAIN', 'ATLANTIC']:
                continue
            
            # Skip callsigns
            if re.match(r'^[KW][A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?$', tag_upper):
                continue
            
            extra_tags.append(f"({tag})")
        
        # Extract ALL quality/bracketed tags
        bracketed_tags = re.findall(r'\[([^\]]+)\]', name)
        for tag in bracketed_tags:
            # Check if tag should be ignored
            if f"[{tag}]" in user_ignored_tags or f"({tag})" in user_ignored_tags:
                continue
            quality_tags.append(f"[{tag}]")
        
        return regional, extra_tags, quality_tags
    
    
    
    def find_best_match(self, query_name, candidate_names, user_ignored_tags=None, remove_cinemax=False,
                        ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True):
        """
        Find the best fuzzy match for a name among a list of candidate names.

        Args:
            query_name: Name to match
            candidate_names: List of candidate names to match against
            user_ignored_tags: User-configured tags to ignore
            remove_cinemax: If True, remove "Cinemax" from candidate names
            ignore_quality: If True, remove ALL quality indicators during normalization
            ignore_regional: If True, remove regional indicator patterns during normalization
            ignore_geographic: If True, remove ALL country code patterns during normalization
            ignore_misc: If True, remove ALL content within parentheses during normalization

        Returns:
            Tuple of (matched_name, score) or (None, 0) if no match found
        """
        if not candidate_names:
            return None, 0

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Normalize the query (channel name - don't remove Cinemax from it)
        normalized_query = self.normalize_name(query_name, user_ignored_tags,
                                                ignore_quality=ignore_quality,
                                                ignore_regional=ignore_regional,
                                                ignore_geographic=ignore_geographic,
                                                ignore_misc=ignore_misc)
        
        if not normalized_query:
            return None, 0

        # Process query for token-sort matching
        processed_query = self.process_string_for_matching(normalized_query)

        # Numeric-sibling guard: when the query contains digit-only tokens (e.g. "Fox Sports 1"),
        # the discriminating digit becomes a single-char edit under token-sort Levenshtein and
        # long shared prefixes mask it — FS1 vs FS2 scores 25/26 = 96% and slips past threshold 95.
        # Require any candidate with digits to share at least one with the query.
        query_digit_tokens = {t for t in normalized_query.split() if t.isdigit()}

        best_score = -1.0
        best_match = None

        for candidate in candidate_names:
            if query_digit_tokens:
                candidate_lower, _ = self._get_cached_norm(candidate, user_ignored_tags)
                if candidate_lower:
                    cand_digit_tokens = {t for t in candidate_lower.split() if t.isdigit()}
                    if not cand_digit_tokens or not (query_digit_tokens & cand_digit_tokens):
                        continue

            # Use cached processed string when available
            processed_candidate = self._get_cached_processed(candidate, user_ignored_tags)
            if not processed_candidate:
                # Fallback: normalize and process on the fly
                candidate_normalized = self.normalize_name(candidate, user_ignored_tags,
                                                            ignore_quality=ignore_quality,
                                                            ignore_regional=ignore_regional,
                                                            ignore_geographic=ignore_geographic,
                                                            ignore_misc=ignore_misc,
                                                            remove_cinemax=remove_cinemax)
                if not candidate_normalized or len(candidate_normalized) < 2:
                    continue
                processed_candidate = self.process_string_for_matching(candidate_normalized)

            score = self.calculate_similarity(processed_query, processed_candidate,
                                              min_ratio=self.match_threshold / 100.0)

            if score > best_score:
                best_score = score
                best_match = candidate
        
        # Convert to percentage and check threshold
        percentage_score = int(best_score * 100)
        
        if percentage_score >= self.match_threshold:
            return best_match, percentage_score
        
        return None, 0
    
    def alias_lookup(self, query_name, candidate_names, alias_map,
                     user_ignored_tags=None, ignore_quality=True, ignore_regional=True,
                     ignore_geographic=True, ignore_misc=True):
        """Exact-normalized alias match.

        Returns the list of candidate_names whose normalized form (spaced OR
        punctuation-stripped) exactly equals the normalized form of any alias
        variant of query_name. Pure; no fuzzy/similarity. Empty list when the
        map is empty or the channel has no alias entry.
        """
        if not alias_map or not candidate_names:
            return []
        variants = alias_map.get(query_name)
        if not variants:
            return []

        def _forms(s):
            n = self.normalize_name(
                s, user_ignored_tags, ignore_quality=ignore_quality,
                ignore_regional=ignore_regional, ignore_geographic=ignore_geographic,
                ignore_misc=ignore_misc)
            if not n:
                return None, None
            low = n.lower()
            return low, re.sub(r'[\s&\-]+', '', low)

        alias_low, alias_nospace = set(), set()
        for v in variants:
            low, nospace = _forms(v)
            if low:
                alias_low.add(low)
                alias_nospace.add(nospace)
        if not alias_low:
            return []

        hits = []
        for cand in candidate_names:
            # Reuse the precompute cache for candidates (mirrors how fuzzy_match
            # normalizes candidates) — avoids re-normalizing every stream name.
            low, nospace = self._get_cached_norm(cand, user_ignored_tags)
            if low and (low in alias_low or nospace in alias_nospace):
                hits.append(cand)
        return hits

    def fuzzy_match(self, query_name, candidate_names, user_ignored_tags=None, remove_cinemax=False,
                    ignore_quality=True, ignore_regional=True, ignore_geographic=True, ignore_misc=True):
        """
        Generic fuzzy matching function that can match any name against a list of candidates.
        This is the main entry point for fuzzy matching.

        Args:
            query_name: Name to match (channel name)
            candidate_names: List of candidate names to match against (stream names)
            user_ignored_tags: User-configured tags to ignore
            remove_cinemax: If True, remove "Cinemax" from candidate names (for channels with "max")
            ignore_quality: If True, remove ALL quality indicators during normalization
            ignore_regional: If True, remove regional indicator patterns during normalization
            ignore_geographic: If True, remove ALL country code patterns during normalization
            ignore_misc: If True, remove ALL content within parentheses during normalization

        Returns:
            Tuple of (matched_name, score, match_type) or (None, 0, None) if no match found
        """
        if not candidate_names:
            return None, 0, None

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Normalize query (channel name - don't remove Cinemax from it)
        normalized_query = self.normalize_name(query_name, user_ignored_tags,
                                                ignore_quality=ignore_quality,
                                                ignore_regional=ignore_regional,
                                                ignore_geographic=ignore_geographic,
                                                ignore_misc=ignore_misc)
        
        if not normalized_query:
            return None, 0, None

        # Numeric-sibling guard: when the query contains digit-only tokens (e.g. "Fox Sports 1"),
        # the discriminating digit becomes a single-char edit under token-sort Levenshtein and
        # long shared prefixes mask it — FS1 vs FS2 scores 25/26 = 96% and slips past threshold 95.
        # Mirrors the inline guard in plugin.py (~2329). Applied to every stage for defense in depth.
        query_digit_tokens = {t for t in normalized_query.split() if t.isdigit()}

        best_match = None
        best_ratio = 0
        match_type = None

        # Stage 1: Exact match (after normalization)
        normalized_query_lower = normalized_query.lower()
        normalized_query_nospace = re.sub(r'[\s&\-]+', '', normalized_query_lower)

        for candidate in candidate_names:
            # Use cached normalization when available
            candidate_lower, candidate_nospace = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue

            if query_digit_tokens:
                cand_digit_tokens = {t for t in candidate_lower.split() if t.isdigit()}
                if not cand_digit_tokens or not (query_digit_tokens & cand_digit_tokens):
                    continue

            # Exact match (space/punctuation insensitive)
            if normalized_query_nospace == candidate_nospace:
                return candidate, 100, "exact"

            # Very high similarity (97%+)
            ratio = self.calculate_similarity(normalized_query_lower, candidate_lower, min_ratio=0.97)
            if ratio >= 0.97 and ratio > best_ratio:
                best_match = candidate
                best_ratio = ratio
                match_type = "exact"

        if best_match:
            return best_match, int(best_ratio * 100), match_type

        # Stage 2: Substring matching
        for candidate in candidate_names:
            # Use cached normalization when available
            candidate_lower, _ = self._get_cached_norm(candidate, user_ignored_tags)
            if not candidate_lower:
                continue

            if query_digit_tokens:
                cand_digit_tokens = {t for t in candidate_lower.split() if t.isdigit()}
                if not cand_digit_tokens or not (query_digit_tokens & cand_digit_tokens):
                    continue

            # Check if one is a substring of the other
            if normalized_query_lower in candidate_lower or candidate_lower in normalized_query_lower:
                length_ratio = min(len(normalized_query_lower), len(candidate_lower)) / max(len(normalized_query_lower), len(candidate_lower))
                if length_ratio >= 0.75:
                    ratio = self.calculate_similarity(normalized_query_lower, candidate_lower,
                                                      min_ratio=self.match_threshold / 100.0)
                    if ratio > best_ratio:
                        best_match = candidate
                        best_ratio = ratio
                        match_type = "substring"

        if best_match and int(best_ratio * 100) >= self.match_threshold:
            return best_match, int(best_ratio * 100), match_type

        # Stage 3: Fuzzy matching with token sorting
        processed_query = self.process_string_for_matching(normalized_query)
        best_score = -1.0
        best_fuzzy = None
        threshold_ratio = self.match_threshold / 100.0

        for candidate in candidate_names:
            if query_digit_tokens:
                candidate_lower, _ = self._get_cached_norm(candidate, user_ignored_tags)
                if candidate_lower:
                    cand_digit_tokens = {t for t in candidate_lower.split() if t.isdigit()}
                    if not cand_digit_tokens or not (query_digit_tokens & cand_digit_tokens):
                        continue

            # Use cached processed string when available
            processed_candidate = self._get_cached_processed(candidate, user_ignored_tags)
            if not processed_candidate:
                continue

            score = self.calculate_similarity(processed_query, processed_candidate,
                                              min_ratio=threshold_ratio)
            if score > best_score:
                best_score = score
                best_fuzzy = candidate

        percentage_score = int(best_score * 100)
        if percentage_score >= self.match_threshold and best_fuzzy:
            return best_fuzzy, percentage_score, f"fuzzy ({percentage_score})"

        return None, 0, None
    
    def match_broadcast_channel(self, channel_name):
        """
        Match broadcast (OTA) channel by callsign.
        
        Args:
            channel_name: Channel name potentially containing a callsign
        
        Returns:
            Tuple of (callsign, station_data) or (None, None) if no match
        """
        callsign = self.extract_callsign(channel_name)
        
        if not callsign:
            return None, None
        
        # Try exact match first
        station = self.channel_lookup.get(callsign)
        
        if station:
            return callsign, station
        
        # Try base callsign (without suffix)
        base_callsign = self.normalize_callsign(callsign)
        station = self.channel_lookup.get(base_callsign)
        
        if station:
            return callsign, station
        
        return callsign, None
    
    def get_category_for_channel(self, channel_name, user_ignored_tags=None):
        """
        Get the category for a channel by matching it in the database.
        
        Args:
            channel_name: Channel name to look up
            user_ignored_tags: User-configured tags to ignore
        
        Returns:
            Category string or None if not found
        """
        if user_ignored_tags is None:
            user_ignored_tags = []
        
        # Try broadcast channel first
        callsign, station = self.match_broadcast_channel(channel_name)
        if station:
            return station.get('category')
        
        # Try premium channel matching
        if self.premium_channels:
            matched_name, score, match_type = self.fuzzy_match(
                channel_name, 
                self.premium_channels, 
                user_ignored_tags
            )
            
            if matched_name:
                # Find the full channel object
                for channel_obj in self.premium_channels_full:
                    if channel_obj.get('channel_name') == matched_name:
                        return channel_obj.get('category')
        
        return None
    
    def build_final_channel_name(self, base_name, regional, extra_tags, quality_tags):
        """
        Build final channel name with regional indicator, extra tags, and quality tags.
        Format: "Channel Name Regional (Extra) [Quality1] [Quality2] ..."
        """
        parts = [base_name]
        
        # Add regional indicator WITHOUT parentheses
        if regional:
            parts.append(regional)
        
        # Add extra tags (already have parentheses)
        if extra_tags:
            parts.extend(extra_tags)
        
        # Add quality tags (preserve original case and count)
        if quality_tags:
            parts.extend(quality_tags)
        
        return " ".join(parts)