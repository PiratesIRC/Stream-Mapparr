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

# Version: YY.DDD.HHMM (Julian date format: Year.DayOfYear.Time)
__version__ = "25.354.1835"

# Setup logging
LOGGER = logging.getLogger("plugins.fuzzy_matcher")

# Categorized regex patterns for granular control during fuzzy matching
# Note: All patterns are applied with re.IGNORECASE flag in normalize_name()

# Quality-related patterns: [4K], HD, (SD), etc.
QUALITY_PATTERNS = [
    # Quality tags in any format: brackets, parentheses, or standalone
    # These patterns match quality tags at the beginning, middle, or end of names
    # Matches: [4K], (4K), 4K, [FHD], (FHD), FHD, etc.
    # Quality keywords list: 4K, 8K, UHD, FHD, HD, SD, FD, Unknown, Unk, Slow, Dead, Backup
    
    # Bracketed quality tags: [4K], [UHD], [FHD], [HD], [SD], etc.
    r'\s*\[(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead|Backup)\]\s*',
    
    # Parenthesized quality tags: (4K), (UHD), (FHD), (HD), (SD), etc.
    r'\s*\((4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead|Backup)\)\s*',
    
    # Standalone quality tags at START of string (with word boundary)
    r'^\s*(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)\b\s*',
    
    # Standalone quality tags at END of string (with word boundary)
    r'\s*\b(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)$',
    
    # Standalone quality tags in MIDDLE (with word boundaries on both sides)
    r'\s+\b(4K|8K|UHD|FHD|HD|SD|FD|Unknown|Unk|Slow|Dead)\b\s+',
]

# Regional indicator patterns: East, West, Pacific, Central, Mountain, Atlantic
REGIONAL_PATTERNS = [
    # Regional: " East" or " east" (word with space prefix)
    r'\s[Ee][Aa][Ss][Tt]',
    # Regional: " West" or " west" (word with space prefix)
    r'\s[Ww][Ee][Ss][Tt]',
    # Regional: " Pacific" or " pacific" (word with space prefix)
    r'\s[Pp][Aa][Cc][Ii][Ff][Ii][Cc]',
    # Regional: " Central" or " central" (word with space prefix)
    r'\s[Cc][Ee][Nn][Tt][Rr][Aa][Ll]',
    # Regional: " Mountain" or " mountain" (word with space prefix)
    r'\s[Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]',
    # Regional: " Atlantic" or " atlantic" (word with space prefix)
    r'\s[Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]',
    # Regional: (East) or (EAST) (parenthesized format)
    r'\s*\([Ee][Aa][Ss][Tt]\)\s*',
    # Regional: (West) or (WEST) (parenthesized format)
    r'\s*\([Ww][Ee][Ss][Tt]\)\s*',
    # Regional: (Pacific) or (PACIFIC) (parenthesized format)
    r'\s*\([Pp][Aa][Cc][Ii][Ff][Ii][Cc]\)\s*',
    # Regional: (Central) or (CENTRAL) (parenthesized format)
    r'\s*\([Cc][Ee][Nn][Tt][Rr][Aa][Ll]\)\s*',
    # Regional: (Mountain) or (MOUNTAIN) (parenthesized format)
    r'\s*\([Mm][Oo][Uu][Nn][Tt][Aa][Ii][Nn]\)\s*',
    # Regional: (Atlantic) or (ATLANTIC) (parenthesized format)
    r'\s*\([Aa][Tt][Ll][Aa][Nn][Tt][Ii][Cc]\)\s*',
]

# Geographic prefix patterns: US:, USA:, etc.
GEOGRAPHIC_PATTERNS = [
    # Country codes in various formats
    # Matches patterns like: US, USA, FR, UK, CA, DE, etc.
    # With separators: US:, USA:, |FR|, US -, FR -, etc.
    
    # Format: XX: or XXX: (e.g., US:, USA:, FR:, UK:)
    # This is safe because the colon clearly indicates a prefix
    r'\b[A-Z]{2,3}:\s*',
    
    # Format: XX - or XXX - (e.g., US - , USA - , FR - )
    # Safe because the dash clearly indicates a separator
    r'\b[A-Z]{2,3}\s*-\s*',
    
    # Format: |XX| or |XXX| (e.g., |US|, |FR|, |UK|)
    # Safe because pipes clearly indicate a tag
    r'\|[A-Z]{2,3}\|\s*',
    
    # Format: [XX] or [XXX] (e.g., [US], [FR], [UK])
    # Safe because brackets clearly indicate a tag
    r'\[[A-Z]{2,3}\]\s*',
]

# Miscellaneous patterns: (CX), (Backup), single-letter tags, etc.
MISC_PATTERNS = [
    # Remove ALL content within parentheses (e.g., (CX), (B), (PRIME), (Backup), etc.)
    r'\s*\([^)]*\)\s*',
]


class FuzzyMatcher:
    """Handles fuzzy matching for channel and stream names with normalization and database loading."""
    
    def __init__(self, plugin_dir=None, match_threshold=85, logger=None):
        """
        Initialize the fuzzy matcher.

        Args:
            plugin_dir: Directory where the plugin and channel JSON files are located (optional)
            match_threshold: Minimum similarity score (0-100) for a match to be accepted
            logger: Logger instance (optional)
        """
        self.plugin_dir = plugin_dir or os.path.dirname(__file__)
        self.match_threshold = match_threshold
        self.logger = logger or LOGGER

        # Channel data storage
        self.broadcast_channels = []  # Channels with callsigns
        self.premium_channels = []  # Channel names only (for fuzzy matching)
        self.premium_channels_full = []  # Full channel objects with category
        self.channel_lookup = {}  # Callsign -> channel data mapping
        self.country_codes = None  # Track which country databases are currently loaded

        # Load all channel databases if plugin_dir is provided
        if self.plugin_dir:
            self._load_channel_databases()
    
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

                for channel in channels_list:
                    channel_type = channel.get('type', '').lower()
                    
                    if 'broadcast' in channel_type or channel_type == 'broadcast (ota)':
                        # Broadcast channel with callsign
                        self.broadcast_channels.append(channel)
                        file_broadcast += 1
                        
                        # Create lookup by callsign
                        callsign = channel.get('callsign', '').strip()
                        if callsign:
                            self.channel_lookup[callsign] = channel
                            
                            # Also store base callsign without suffix for easier matching
                            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                            if base_callsign != callsign:
                                self.channel_lookup[base_callsign] = channel
                    else:
                        # Premium/cable/national channel
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
        return True

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

                for channel in channels_list:
                    channel_type = channel.get('type', '').lower()

                    if 'broadcast' in channel_type or channel_type == 'broadcast (ota)':
                        # Broadcast channel with callsign
                        self.broadcast_channels.append(channel)
                        file_broadcast += 1

                        # Create lookup by callsign
                        callsign = channel.get('callsign', '').strip()
                        if callsign:
                            self.channel_lookup[callsign] = channel

                            # Also store base callsign without suffix for easier matching
                            base_callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
                            if base_callsign != callsign:
                                self.channel_lookup[base_callsign] = channel
                    else:
                        # Premium/cable/national channel
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
        return True

    def extract_callsign(self, channel_name):
        """
        Extract US TV callsign from channel name with priority order.
        Returns None if common false positives appear alone.
        """
        # Remove common prefixes
        channel_name = re.sub(r'^D\d+-', '', channel_name)
        channel_name = re.sub(r'^USA?\s*[^a-zA-Z0-9]*\s*', '', channel_name, flags=re.IGNORECASE)
        
        # Priority 1: Callsigns in parentheses (most reliable)
        paren_match = re.search(r'\(([KW][A-Z]{3})(?:-[A-Z\s]+)?\)', channel_name, re.IGNORECASE)
        if paren_match:
            callsign = paren_match.group(1).upper()
            if callsign not in ['WEST', 'EAST', 'KIDS', 'WOMEN', 'WILD', 'WORLD']:
                return callsign
        
        # Priority 2: Callsigns with suffix in parentheses
        paren_suffix_match = re.search(r'\(([KW][A-Z]{2,4}-(?:TV|CD|LP|DT|LD))\)', channel_name, re.IGNORECASE)
        if paren_suffix_match:
            callsign = paren_suffix_match.group(1).upper()
            return callsign
        
        # Priority 3: Callsigns at the end
        end_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\s*(?:\.[a-z]+)?\s*$', channel_name, re.IGNORECASE)
        if end_match:
            callsign = end_match.group(1).upper()
            if callsign not in ['WEST', 'EAST', 'KIDS', 'WOMEN', 'WILD', 'WORLD']:
                return callsign
        
        # Priority 4: Any word matching callsign pattern
        word_match = re.search(r'\b([KW][A-Z]{2,4}(?:-(?:TV|CD|LP|DT|LD))?)\b', channel_name, re.IGNORECASE)
        if word_match:
            callsign = word_match.group(1).upper()
            if callsign not in ['WEST', 'EAST', 'KIDS', 'WOMEN', 'WILD', 'WORLD']:
                return callsign
        
        return None
    
    def normalize_callsign(self, callsign):
        """Remove suffix from callsign for display."""
        if callsign:
            callsign = re.sub(r'-(?:TV|CD|LP|DT|LD)$', '', callsign)
        return callsign
    
    def normalize_name(self, name, user_ignored_tags=None, ignore_quality=True, ignore_regional=True,
                       ignore_geographic=True, ignore_misc=True, remove_cinemax=False, remove_country_prefix=False):
        """
        Normalize channel or stream name for matching by removing tags, prefixes, and other noise.

        Args:
            name: Name to normalize
            user_ignored_tags: Additional user-configured tags to ignore (list of strings)
            ignore_quality: If True, remove ALL quality indicators in any format (e.g., 4K, [4K], (4K), FHD, [FHD], (FHD), HD, SD, UHD, 8K)
            ignore_regional: If True, remove regional indicator patterns (e.g., East)
            ignore_geographic: If True, remove ALL country code patterns (e.g., US, USA, US:, |FR|, FR -, [UK])
            ignore_misc: If True, remove ALL content within parentheses (e.g., (CX), (B), (PRIME), (Backup))
            remove_cinemax: If True, remove "Cinemax" prefix (useful when channel name contains "max")
            remove_country_prefix: If True, remove country code prefixes (e.g., CA:, UK , DE: ) from start of name

        Returns:
            Normalized name
        """
        if user_ignored_tags is None:
            user_ignored_tags = []

        # Store original for logging
        original_name = name

        # CRITICAL FIX: Normalize spacing around numbers FIRST, before any other processing
        # This ensures "ITV1" and "ITV 1" are treated identically during matching
        # Pattern: Insert space before number if preceded by letter, and after number if followed by letter
        # Examples: "ITV1" -> "ITV 1", "BBC2" -> "BBC 2", "E4" -> "E 4"
        name = re.sub(r'([a-zA-Z])(\d)', r'\1 \2', name)  # Letter followed by digit
        name = re.sub(r'(\d)([a-zA-Z])', r'\1 \2', name)  # Digit followed by letter
        
        # CRITICAL FIX: Normalize hyphens to spaces for better token matching
        # This ensures "UK-ITV" becomes "UK ITV" and matches properly
        # Common patterns: "UK-ITV 1", "US-CNN", etc.
        name = re.sub(r'-', ' ', name)  # Digit followed by letter
        
        # Remove ALL leading parenthetical prefixes like (US) (PRIME2), (SP2), (D1), etc.
        # Loop until no more leading parentheses are found
        while name.lstrip().startswith('('):
            new_name = re.sub(r'^\s*\([^\)]+\)\s*', '', name)
            if new_name == name:  # No change, break to avoid infinite loop
                break
            name = new_name

        # Remove country code prefix if requested (e.g., "CA:", "UK ", "USA: ")
        # This handles multi-country databases where streams may be prefixed with country codes
        if remove_country_prefix:
            # Known quality tags that should NOT be removed (to avoid false positives)
            quality_tags = {'HD', 'SD', 'FD', 'UHD', 'FHD'}

            # Check for 2-3 letter prefix with colon or space at start
            # Fixed regex: [:\s] instead of [:|\\s] (pipe and backslash were incorrect)
            prefix_match = re.match(r'^([A-Z]{2,3})[:\s]\s*', name)
            if prefix_match:
                prefix = prefix_match.group(1).upper()
                # Only remove if it's NOT a quality tag
                if prefix not in quality_tags:
                    name = name[len(prefix_match.group(0)):]

        # Remove "Cinemax" prefix if requested (for channels containing "max")
        if remove_cinemax:
            name = re.sub(r'\bCinemax\b\s*', '', name, flags=re.IGNORECASE)

        # Build list of patterns to apply based on category flags
        patterns_to_apply = []

        if ignore_quality:
            patterns_to_apply.extend(QUALITY_PATTERNS)

        if ignore_regional:
            patterns_to_apply.extend(REGIONAL_PATTERNS)

        if ignore_geographic:
            patterns_to_apply.extend(GEOGRAPHIC_PATTERNS)

        if ignore_misc:
            patterns_to_apply.extend(MISC_PATTERNS)

        # Apply selected hardcoded patterns
        for pattern in patterns_to_apply:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Apply user-configured ignored tags with improved handling
        for tag in user_ignored_tags:
            # Check if tag contains brackets or parentheses - if so, match literally
            if '[' in tag or ']' in tag or '(' in tag or ')' in tag:
                # Literal match for bracketed/parenthesized tags
                escaped_tag = re.escape(tag)
                name = re.sub(escaped_tag, '', name, flags=re.IGNORECASE)
            else:
                # Word boundary match for simple word tags to avoid partial matches
                # e.g., "East" won't match the "east" in "Feast"
                escaped_tag = re.escape(tag)
                name = re.sub(r'\b' + escaped_tag + r'\b', '', name, flags=re.IGNORECASE)
        
        # Remove callsigns in parentheses
        name = re.sub(r'\([KW][A-Z]{3}(?:-(?:TV|CD|LP|DT|LD))?\)', '', name, flags=re.IGNORECASE)
        
        # Remove other tags in parentheses
        name = re.sub(r'\([A-Z0-9]+\)', '', name)
        
        # Remove common pattern fixes
        name = re.sub(r'^The\s+', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Network\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+Channel\s*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+TV\s*$', '', name, flags=re.IGNORECASE)
        
        # Clean up whitespace
        name = re.sub(r'\s+', ' ', name).strip()

        # Log debug message if normalization resulted in empty string (indicates overly aggressive stripping)
        if not name:
            self.logger.debug(f"normalize_name returned empty string for input: '{original_name}' (original input was stripped too aggressively)")

        return name
    
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
    
    def calculate_similarity(self, str1, str2):
        """
        Calculate Levenshtein distance-based similarity ratio between two strings.

        Returns:
            Similarity ratio between 0.0 and 1.0
        """
        if len(str1) < len(str2):
            str1, str2 = str2, str1

        # Empty strings should not match anything (including other empty strings)
        # This prevents false positives when normalization strips everything
        if len(str2) == 0 or len(str1) == 0:
            return 0.0
        
        previous_row = list(range(len(str2) + 1))
        
        for i, c1 in enumerate(str1):
            current_row = [i + 1]
            for j, c2 in enumerate(str2):
                insertions = previous_row[j + 1] + 1
                deletions = current_row[j] + 1
                substitutions = previous_row[j] + (c1 != c2)
                current_row.append(min(insertions, deletions, substitutions))
            previous_row = current_row
        
        distance = previous_row[-1]
        total_len = len(str1) + len(str2)
        
        if total_len == 0:
            return 1.0
        
        ratio = (total_len - distance) / total_len
        return ratio
    
    def process_string_for_matching(self, s):
        """
        Normalize a string for token-sort fuzzy matching.
        Lowercases, removes accents, removes punctuation, sorts tokens.
        Properly handles Unicode characters (e.g., French accents).
        Normalizes spacing around numbers to handle "ITV1" vs "ITV 1" cases.
        """
        # First, normalize Unicode to decomposed form (NFD)
        # This separates base characters from accent marks
        # e.g., "é" becomes "e" + combining acute accent
        s = unicodedata.normalize('NFD', s)
        
        # Remove combining characters (accent marks)
        # Keep only base characters
        s = ''.join(char for char in s if unicodedata.category(char) != 'Mn')
        
        # Convert to lowercase
        s = s.lower()
        
        # Normalize spacing around numbers: add space before numbers if not already present
        # This makes "itv1" and "itv 1" equivalent after tokenization
        # Pattern: letter followed immediately by digit -> insert space between them
        s = re.sub(r'([a-z])(\d)', r'\1 \2', s)
        
        # Replace non-alphanumeric with space
        cleaned_s = ""
        for char in s:
            if 'a' <= char <= 'z' or '0' <= char <= '9':
                cleaned_s += char
            else:
                cleaned_s += ' '
        
        # Split, sort, and rejoin
        tokens = sorted([token for token in cleaned_s.split() if token])
        return " ".join(tokens)
    
    def find_best_match(self, query_name, candidate_names, user_ignored_tags=None, remove_cinemax=False):
        """
        Find the best fuzzy match for a name among a list of candidate names.

        Args:
            query_name: Name to match
            candidate_names: List of candidate names to match against
            user_ignored_tags: User-configured tags to ignore
            remove_cinemax: If True, remove "Cinemax" from candidate names

        Returns:
            Tuple of (matched_name, score) or (None, 0) if no match found
        """
        if not candidate_names:
            return None, 0

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Normalize the query (channel name - don't remove Cinemax from it)
        normalized_query = self.normalize_name(query_name, user_ignored_tags)
        
        if not normalized_query:
            return None, 0
        
        # Process query for token-sort matching
        processed_query = self.process_string_for_matching(normalized_query)

        best_score = -1.0
        best_match = None

        for candidate in candidate_names:
            # Normalize candidate (stream name) with Cinemax removal if requested
            candidate_normalized = self.normalize_name(candidate, user_ignored_tags, remove_cinemax=remove_cinemax)

            # Skip candidates that normalize to empty or very short strings
            if not candidate_normalized or len(candidate_normalized) < 2:
                continue

            processed_candidate = self.process_string_for_matching(candidate_normalized)
            score = self.calculate_similarity(processed_query, processed_candidate)

            if score > best_score:
                best_score = score
                best_match = candidate
        
        # Convert to percentage and check threshold
        percentage_score = int(best_score * 100)
        
        if percentage_score >= self.match_threshold:
            return best_match, percentage_score
        
        return None, 0
    
    def fuzzy_match(self, query_name, candidate_names, user_ignored_tags=None, remove_cinemax=False):
        """
        Generic fuzzy matching function that can match any name against a list of candidates.
        This is the main entry point for fuzzy matching.

        Args:
            query_name: Name to match (channel name)
            candidate_names: List of candidate names to match against (stream names)
            user_ignored_tags: User-configured tags to ignore
            remove_cinemax: If True, remove "Cinemax" from candidate names (for channels with "max")

        Returns:
            Tuple of (matched_name, score, match_type) or (None, 0, None) if no match found
        """
        if not candidate_names:
            return None, 0, None

        if user_ignored_tags is None:
            user_ignored_tags = []

        # Normalize query (channel name - don't remove Cinemax from it)
        normalized_query = self.normalize_name(query_name, user_ignored_tags)
        
        if not normalized_query:
            return None, 0, None
        
        best_match = None
        best_ratio = 0
        match_type = None
        
        # Stage 1: Exact match (after normalization)
        normalized_query_lower = normalized_query.lower()
        normalized_query_nospace = re.sub(r'[\s&\-]+', '', normalized_query_lower)

        for candidate in candidate_names:
            # Normalize candidate (stream name) with Cinemax removal if requested
            candidate_normalized = self.normalize_name(candidate, user_ignored_tags, remove_cinemax=remove_cinemax)

            # Skip candidates that normalize to empty or very short strings (< 2 chars)
            # This prevents false positives where multiple streams all normalize to ""
            if not candidate_normalized or len(candidate_normalized) < 2:
                continue

            candidate_lower = candidate_normalized.lower()
            candidate_nospace = re.sub(r'[\s&\-]+', '', candidate_lower)

            # Exact match
            if normalized_query_nospace == candidate_nospace:
                return candidate, 100, "exact"

            # Very high similarity (97%+)
            ratio = self.calculate_similarity(normalized_query_lower, candidate_lower)
            if ratio >= 0.97 and ratio > best_ratio:
                best_match = candidate
                best_ratio = ratio
                match_type = "exact"

        if best_match:
            return best_match, int(best_ratio * 100), match_type

        # Stage 2: Substring matching
        for candidate in candidate_names:
            # Normalize candidate (stream name) with Cinemax removal if requested
            candidate_normalized = self.normalize_name(candidate, user_ignored_tags, remove_cinemax=remove_cinemax)

            # Skip candidates that normalize to empty or very short strings
            if not candidate_normalized or len(candidate_normalized) < 2:
                continue

            candidate_lower = candidate_normalized.lower()

            # Check if one is a substring of the other
            if normalized_query_lower in candidate_lower or candidate_lower in normalized_query_lower:
                # Calculate similarity score
                ratio = self.calculate_similarity(normalized_query_lower, candidate_lower)
                if ratio > best_ratio:
                    best_match = candidate
                    best_ratio = ratio
                    match_type = "substring"

        if best_match and int(best_ratio * 100) >= self.match_threshold:
            return best_match, int(best_ratio * 100), match_type

        # Stage 3: Fuzzy matching with token sorting
        fuzzy_match, score = self.find_best_match(query_name, candidate_names, user_ignored_tags, remove_cinemax=remove_cinemax)
        if fuzzy_match:
            return fuzzy_match, score, f"fuzzy ({score})"
        
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