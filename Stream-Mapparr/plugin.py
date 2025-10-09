"""
Dispatcharr Stream-Mapparr Plugin
Automatically adds matching streams to channels based on name similarity and quality
"""

import logging
import json
import csv
import os
import re
import requests
from datetime import datetime
from django.utils import timezone

# Django model imports
from apps.channels.models import Channel, Stream, ChannelStream, ChannelProfileMembership

# Setup logging using Dispatcharr's format
LOGGER = logging.getLogger("plugins.stream_mapparr")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

class Plugin:
    """Dispatcharr Stream-Mapparr Plugin"""
    
    name = "Stream-Mapparr"
    version = "0.2"
    description = "Automatically add matching streams to channels based on name similarity and quality precedence"
    
    # Settings rendered by UI
    fields = [
        {
            "id": "dispatcharr_url",
            "label": "Dispatcharr URL",
            "type": "string",
            "default": "",
            "placeholder": "http://192.168.1.10:9191",
            "help_text": "URL of your Dispatcharr instance (from your browser address bar). Example: http://127.0.0.1:9191",
        },
        {
            "id": "dispatcharr_username",
            "label": "Dispatcharr Admin Username",
            "type": "string",
            "help_text": "Your admin username for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "dispatcharr_password",
            "label": "Dispatcharr Admin Password",
            "type": "string",
            "input_type": "password",
            "help_text": "Your admin password for the Dispatcharr UI. Required for API access.",
        },
        {
            "id": "profile_name",
            "label": "Profile Name",
            "type": "string",
            "default": "",
            "placeholder": "Sports",
            "help_text": "*** Required Field *** - The name of an existing Channel Profile to process channels from.",
        },
        {
            "id": "selected_groups",
            "label": "Channel Groups (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "Sports, News, Entertainment",
            "help_text": "Specific channel groups to process, or leave empty for all groups.",
        },
        {
            "id": "ignore_tags",
            "label": "Ignore Tags (comma-separated)",
            "type": "string",
            "default": "",
            "placeholder": "4K, [4K], [Dead]",
            "help_text": "Tags to ignore when matching streams. Space-separated in channel names unless they contain brackets/parentheses.",
        },
        {
            "id": "visible_channel_limit",
            "label": "Visible Channel Limit",
            "type": "number",
            "default": 1,
            "help_text": "Number of channels that will be visible and have streams added. Channels are prioritized by quality tags, then by channel number.",
        },
    ]
    
    # Actions for Dispatcharr UI
    actions = [
        {
            "id": "load_process_channels",
            "label": "Load/Process Channels",
            "description": "Validate settings and load channels from the specified profile and groups",
        },
        {
            "id": "preview_changes",
            "label": "Preview Changes (Dry Run)",
            "description": "Preview which streams will be added to channels without making changes",
        },
        {
            "id": "add_streams_to_channels",
            "label": "Add Stream(s) to Channels",
            "description": "Add matching streams to channels and replace existing stream assignments",
            "confirm": {
                "required": True,
                "title": "Add Streams to Channels?",
                "message": "This will replace existing stream assignments on matching channels. Continue?"
            }
        },
        {
            "id": "manage_channel_visibility",
            "label": "Manage Channel Visibility",
            "description": "Disable all channels, then enable only channels with 0 or 1 stream (excluding channels attached to others)",
            "confirm": {
                "required": True,
                "title": "Manage Channel Visibility?",
                "message": "This will disable ALL channels in the profile, then enable only channels with 0 or 1 stream that are not attached to other channels. Continue?"
            }
        },
    ]
    
    # Quality precedence order for channel tags
    CHANNEL_QUALITY_TAG_ORDER = ["[4K]", "[FHD]", "[HD]", "[SD]", "[Unknown]", "[Slow]", ""]
    
    # Quality precedence order for stream tags (brackets and parentheses)
    STREAM_QUALITY_ORDER = [
        "[4K]", "(4K)", "4K",
        "[FHD]", "(FHD)", "FHD",
        "[HD]", "(HD)", "HD", "(H)",
        "[SD]", "(SD)", "SD",
        "(F)",
        "(D)",
        "Slow", "[Slow]", "(Slow)"
    ]
    
    def __init__(self):
        self.processed_data_file = "/data/stream_mapparr_processed.json"
        self.loaded_channels = []
        self.loaded_streams = []
        self.channel_stream_matches = []
        
        LOGGER.info(f"{self.name} Plugin v{self.version} initialized")

    def _get_api_token(self, settings, logger):
        """Get an API access token using username and password."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        username = settings.get("dispatcharr_username", "")
        password = settings.get("dispatcharr_password", "")

        if not all([dispatcharr_url, username, password]):
            return None, "Dispatcharr URL, Username, and Password must be configured in the plugin settings."

        try:
            url = f"{dispatcharr_url}/api/accounts/token/"
            payload = {"username": username, "password": password}
            
            logger.info(f"Attempting to authenticate with Dispatcharr at: {url}")
            response = requests.post(url, json=payload, timeout=15)

            if response.status_code == 401:
                logger.error("Authentication failed - invalid credentials")
                return None, "Authentication failed. Please check your username and password in the plugin settings."
            elif response.status_code == 404:
                logger.error(f"API endpoint not found - check Dispatcharr URL: {dispatcharr_url}")
                return None, f"API endpoint not found. Please verify your Dispatcharr URL: {dispatcharr_url}"
            elif response.status_code >= 500:
                logger.error(f"Server error from Dispatcharr: {response.status_code}")
                return None, f"Dispatcharr server error ({response.status_code}). Please check if Dispatcharr is running properly."
            
            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access")

            if not access_token:
                logger.error("No access token returned from API")
                return None, "Login successful, but no access token was returned by the API."
            
            logger.info("Successfully obtained API access token")
            return access_token, None
            
        except requests.exceptions.ConnectionError as e:
            logger.error(f"Connection error: {e}")
            return None, f"Unable to connect to Dispatcharr at {dispatcharr_url}. Please check the URL and ensure Dispatcharr is running."
        except requests.exceptions.Timeout as e:
            logger.error(f"Request timeout: {e}")
            return None, "Request timed out while connecting to Dispatcharr. Please check your network connection."
        except requests.RequestException as e:
            logger.error(f"Request error: {e}")
            return None, f"Network error occurred while authenticating: {e}"
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response: {e}")
            return None, "Invalid response from Dispatcharr API. Please check if the URL is correct."
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            return None, f"Unexpected error during authentication: {e}"

    def _get_api_data(self, endpoint, token, settings, logger):
        """Helper to perform GET requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}
        
        try:
            logger.info(f"Making API request to: {endpoint}")
            response = requests.get(url, headers=headers, timeout=30)
            
            if response.status_code == 401:
                logger.error("API token expired or invalid")
                raise Exception("API authentication failed. Token may have expired.")
            elif response.status_code == 403:
                logger.error("API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")
            
            response.raise_for_status()
            json_data = response.json()
            
            if isinstance(json_data, dict):
                return json_data.get('results', json_data)
            elif isinstance(json_data, list):
                return json_data
            return []
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed for {endpoint}: {e}")
            raise Exception(f"API request failed: {e}")

    def _patch_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform PATCH requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        
        try:
            logger.info(f"Making API PATCH request to: {endpoint}")
            response = requests.patch(url, headers=headers, json=payload, timeout=60)
            
            if response.status_code == 401:
                logger.error("API token expired or invalid")
                raise Exception("API authentication failed. Token may have expired.")
            elif response.status_code == 403:
                logger.error("API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API PATCH request failed for {endpoint}: {e}")
            raise Exception(f"API PATCH request failed: {e}")

    def _post_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform POST requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url", "").strip().rstrip('/')
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
        
        try:
            logger.info(f"Making API POST request to: {endpoint}")
            response = requests.post(url, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 401:
                logger.error("API token expired or invalid")
                raise Exception("API authentication failed. Token may have expired.")
            elif response.status_code == 403:
                logger.error("API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")
            
            response.raise_for_status()
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"API POST request failed for {endpoint}: {e}")
            raise Exception(f"API POST request failed: {e}")

    def _trigger_m3u_refresh(self, token, settings, logger):
        """Triggers a global M3U refresh to update the GUI via WebSockets."""
        logger.info("Triggering M3U refresh to update the GUI...")
        try:
            self._post_api_data("/api/m3u/refresh/", token, {}, settings, logger)
            logger.info("M3U refresh triggered successfully.")
            return True
        except Exception as e:
            logger.error(f"Failed to trigger M3U refresh: {e}")
            return False

    def _clean_channel_name(self, name, ignore_tags=None):
        """Remove brackets and their contents from channel name for matching, and remove ignore tags."""
        if ignore_tags is None:
            ignore_tags = []
        
        # Remove anything in square brackets or parentheses at the end
        cleaned = re.sub(r'\s*[\[\(][^\[\]\(\)]*[\]\)]\s*$', '', name)
        # Keep removing until no more brackets at the end
        while True:
            new_cleaned = re.sub(r'\s*[\[\(][^\[\]\(\)]*[\]\)]\s*$', '', cleaned)
            if new_cleaned == cleaned:
                break
            cleaned = new_cleaned
        
        # Remove ignore tags
        for tag in ignore_tags:
            # If tag has brackets/parentheses, match exactly
            if '[' in tag or ']' in tag or '(' in tag or ')' in tag:
                # Escape special regex characters
                escaped_tag = re.escape(tag)
                cleaned = re.sub(r'\s*' + escaped_tag + r'\s*', ' ', cleaned, flags=re.IGNORECASE)
            else:
                # Match as space-separated word
                cleaned = re.sub(r'\b' + re.escape(tag) + r'\b', '', cleaned, flags=re.IGNORECASE)
        
        return cleaned.strip()

    def _extract_quality(self, stream_name):
        """Extract quality indicator from stream name."""
        for quality in self.STREAM_QUALITY_ORDER:
            # Match quality with or without brackets/parentheses
            if quality in ["(H)", "(F)", "(D)"]:
                # Special handling for single-letter quality indicators
                if quality in stream_name:
                    return quality
            else:
                quality_clean = quality.strip('[]()').strip()
                patterns = [
                    f"\\[{quality_clean}\\]",
                    f"\\({quality_clean}\\)",
                    f"\\b{quality_clean}\\b"
                ]
                for pattern in patterns:
                    if re.search(pattern, stream_name, re.IGNORECASE):
                        return quality
        return None

    def _extract_channel_quality_tag(self, channel_name):
        """Extract quality tag from channel name for prioritization."""
        for tag in self.CHANNEL_QUALITY_TAG_ORDER:
            if tag == "":
                # Check if channel has no quality tag
                has_tag = False
                for check_tag in self.CHANNEL_QUALITY_TAG_ORDER[:-1]:  # Exclude blank
                    if check_tag in channel_name:
                        has_tag = True
                        break
                if not has_tag:
                    return ""
            elif tag in channel_name:
                return tag
        return ""

    def _sort_channels_by_priority(self, channels):
        """Sort channels by quality tag priority, then by channel number."""
        def get_priority_key(channel):
            quality_tag = self._extract_channel_quality_tag(channel['name'])
            try:
                quality_index = self.CHANNEL_QUALITY_TAG_ORDER.index(quality_tag)
            except ValueError:
                quality_index = len(self.CHANNEL_QUALITY_TAG_ORDER)
            
            # Get channel number, default to 999999 if not available
            channel_number = channel.get('channel_number', 999999)
            if channel_number is None:
                channel_number = 999999
            
            return (quality_index, channel_number)
        
        return sorted(channels, key=get_priority_key)

    def _sort_streams_by_quality(self, streams):
        """Sort streams by quality precedence."""
        def get_quality_index(stream):
            quality = self._extract_quality(stream['name'])
            if quality:
                try:
                    return self.STREAM_QUALITY_ORDER.index(quality)
                except ValueError:
                    return len(self.STREAM_QUALITY_ORDER)
            return len(self.STREAM_QUALITY_ORDER)
        
        return sorted(streams, key=get_quality_index)

    def _load_channel_list(self, logger):
        """Load channel names from channels.txt file for precise matching."""
        channels_file = os.path.join(os.path.dirname(__file__), 'channels.txt')
        channel_names = []
        
        try:
            if os.path.exists(channels_file):
                with open(channels_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if line:  # Skip empty lines
                            channel_names.append(line)
                logger.info(f"Loaded {len(channel_names)} channel names from channels.txt")
            else:
                logger.warning(f"channels.txt not found at {channels_file}")
        except Exception as e:
            logger.error(f"Error loading channels.txt: {e}")
        
        return channel_names

    def _is_ota_channel(self, channel_name):
        """Check if a channel name matches OTA pattern."""
        # OTA pattern: "NETWORK - STATE City (CALLSIGN)" with optional quality tags
        # Examples: "ABC - TN Chattanooga (WTVC)", "NBC - NY New York (WNBC) [HD]"
        ota_pattern = r'^[A-Z]+\s*-\s*[A-Z]{2}\s+.+\([A-Z]+.*?\)'
        return bool(re.search(ota_pattern, channel_name))

    def _extract_ota_info(self, channel_name):
        """Extract network, state, city, and callsign from OTA channel name."""
        # Pattern: "NETWORK - STATE City (CALLSIGN)" with optional quality tags after
        # Match: CBS - IN South Bend (WSBT) [HD]
        match = re.match(r'^([A-Z]+)\s*-\s*([A-Z]{2})\s+([^(]+)\(([A-Z][A-Z0-9-]+)\)', channel_name)
        if match:
            network = match.group(1).strip().upper()
            state = match.group(2).strip().upper()
            city = match.group(3).strip().upper()
            callsign = match.group(4).strip().upper()
            
            # Clean callsign (remove anything after dash)
            callsign = self._parse_callsign(callsign)
            
            return {
                'network': network,
                'state': state,
                'city': city,
                'callsign': callsign
            }
        return None

    def _parse_callsign(self, callsign):
        """Extract clean callsign, removing suffixes after dash."""
        if not callsign:
            return None
        
        # Remove anything after dash (e.g., "WLNE-TV" becomes "WLNE")
        if '-' in callsign:
            callsign = callsign.split('-')[0].strip()
        
        return callsign.upper()

    def _match_streams_to_channel(self, channel, all_streams, logger, ignore_tags=None, known_channels=None, networks_data=None):
        """Find matching streams for a channel based on name similarity."""
        if ignore_tags is None:
            ignore_tags = []
        if known_channels is None:
            known_channels = []
        if networks_data is None:
            networks_data = []
        
        channel_name = channel['name']
        
        # FIRST: Check if this is an OTA channel and try callsign matching
        if self._is_ota_channel(channel_name):
            logger.info(f"Matching OTA channel: {channel_name}")
            
            # Extract callsign from channel name BEFORE cleaning
            ota_info = self._extract_ota_info(channel_name)
            if ota_info:
                callsign = ota_info['callsign']
                logger.info(f"  Extracted callsign: {callsign}")
                
                # Search for streams containing the callsign
                matching_streams = []
                callsign_pattern = r'\b' + re.escape(callsign) + r'\b'
                
                for stream in all_streams:
                    stream_name = stream['name']
                    
                    # Check if stream contains the callsign
                    if re.search(callsign_pattern, stream_name, re.IGNORECASE):
                        matching_streams.append(stream)
                        logger.info(f"  Found callsign match: {stream_name}")
                
                if matching_streams:
                    sorted_streams = self._sort_streams_by_quality(matching_streams)
                    logger.info(f"  Sorted {len(sorted_streams)} streams by quality (callsign matching)")
                    return sorted_streams
                else:
                    logger.info(f"  No streams found with callsign {callsign}")
                    # Try networks.json fallback
                    logger.info(f"  Trying networks.json OTA matching as fallback")
                    matched_streams = self._match_ota_streams(channel_name, all_streams, networks_data, logger)
                    
                    if matched_streams:
                        sorted_streams = self._sort_streams_by_quality(matched_streams)
                        logger.info(f"  Sorted {len(sorted_streams)} OTA streams by quality")
                        return sorted_streams
            else:
                logger.info(f"  Could not extract OTA info from: {channel_name}")
        
        # SECOND: Regular channel matching logic (only if not OTA or OTA matching failed)
        cleaned_channel_name = self._clean_channel_name(channel_name, ignore_tags)
        logger.info(f"Matching streams for channel: {channel_name}")
        logger.info(f"  Cleaned channel name: {cleaned_channel_name}")
        
        # Debug: show first few stream names to verify we have streams
        if all_streams:
            sample_streams = [s['name'] for s in all_streams[:5]]
            logger.info(f"  Sample streams available: {sample_streams}")
        else:
            logger.info(f"  No streams available to match against")
            return []
        
        matching_streams = []
        
        # Escape special regex characters in channel name for word boundary matching
        escaped_channel_name = re.escape(cleaned_channel_name)
        
        # Build a list of longer channel names that contain our channel name
        longer_channels = []
        if known_channels:
            for known_channel in known_channels:
                if cleaned_channel_name.lower() != known_channel.lower():
                    if cleaned_channel_name.lower() in known_channel.lower():
                        longer_channels.append(known_channel)
        
        if longer_channels:
            logger.info(f"  Longer channels to exclude: {longer_channels}")
        
        for stream in all_streams:
            stream_name = stream['name']
            stream_cleaned = self._clean_channel_name(stream_name, ignore_tags)
            
            # Simple exact match after cleaning (case insensitive)
            if cleaned_channel_name.lower() == stream_cleaned.lower():
                logger.info(f"  Found exact match: {stream_name}")
                matching_streams.append(stream)
                continue
            
            # Pattern matching - more flexible
            # The pattern looks for the cleaned channel name as a substring
            # It can be at the start, after a colon/space, or standalone
            pattern = r'(?:^|[:\s])\s*' + escaped_channel_name + r'(?:\s*[\[\(\|]|$)'
            
            if re.search(pattern, stream_name, re.IGNORECASE):
                # Filter 1: Skip if it is part of a call sign
                call_sign_pattern = r'\b[A-Z]{4,5}\s+' + escaped_channel_name + r'\b'
                if re.search(call_sign_pattern, stream_name):
                    logger.info(f"  Skipped (call sign): {stream_name}")
                    continue
                
                # Filter 2: Skip if stream actually contains a longer channel name
                skip_stream = False
                for longer_channel in longer_channels:
                    escaped_longer = re.escape(longer_channel)
                    longer_pattern = r'(?:^|[:\s])\s*' + escaped_longer + r'(?:\s*[\[\(\|]|$)'
                    if re.search(longer_pattern, stream_name, re.IGNORECASE):
                        logger.info(f"  Skipped (matches longer channel '{longer_channel}'): {stream_name}")
                        skip_stream = True
                        break
                
                if skip_stream:
                    continue
                
                # Filter 3: Skip West/Pacific timezone feeds unless channel explicitly mentions West
                channel_is_west = bool(re.search(r'\b(west|pacific|pst)\b', cleaned_channel_name, re.IGNORECASE))
                stream_is_west = bool(re.search(r'\(west\)|\(pacific\)|\(pst\)', stream_name, re.IGNORECASE))
                
                if stream_is_west and not channel_is_west:
                    logger.info(f"  Skipped (West coast feed): {stream_name}")
                    continue
                
                matching_streams.append(stream)
                logger.info(f"  Found pattern match: {stream_name}")
        
        # Return all matching streams sorted by quality
        if matching_streams:
            sorted_streams = self._sort_streams_by_quality(matching_streams)
            logger.info(f"  Sorted {len(sorted_streams)} streams by quality (regular matching)")
            return sorted_streams
        
        logger.info(f"  No matching streams found")
        return []

    def _load_networks_data(self, logger):
        """Load OTA network data from networks.json file."""
        networks_file = os.path.join(os.path.dirname(__file__), 'networks.json')
        networks_data = []
        
        try:
            if os.path.exists(networks_file):
                with open(networks_file, 'r', encoding='utf-8') as f:
                    networks_data = json.load(f)
                logger.info(f"Loaded {len(networks_data)} network entries from networks.json")
            else:
                logger.warning(f"networks.json not found at {networks_file}")
        except Exception as e:
            logger.error(f"Error loading networks.json: {e}")
        
        return networks_data

    def _parse_network_affiliation(self, network_affiliation):
        """Extract the primary network from network_affiliation field."""
        if not network_affiliation:
            return None
        
        # Handle patterns like "CBS (12.1), CW (12.2), MeTV (12.3)"
        # Extract text before the first parenthesis
        if '(' in network_affiliation:
            network_affiliation = network_affiliation.split('(')[0].strip()
        
        # Handle patterns like "WTOV D1 - NBC; WTOV D2 - FOX"
        # Extract text after the last dash before the first separator
        if ' - ' in network_affiliation:
            # Split by semicolons/slashes first to get the first segment
            for separator in [';', '/']:
                if separator in network_affiliation:
                    network_affiliation = network_affiliation.split(separator)[0].strip()
                    break
            # Now extract after the dash
            if ' - ' in network_affiliation:
                network_affiliation = network_affiliation.split(' - ')[-1].strip()
        
        # Split by separators: comma, semicolon, or slash
        for separator in [',', ';', '/']:
            if separator in network_affiliation:
                network_affiliation = network_affiliation.split(separator)[0].strip()
                break
        
        return network_affiliation.upper()

    def _match_ota_streams(self, channel_name, all_streams, networks_data, logger):
        """Match OTA channel to streams using networks.json data."""
        ota_info = self._extract_ota_info(channel_name)
        if not ota_info:
            logger.info(f"  Could not parse OTA info from: {channel_name}")
            return []
        
        logger.info(f"  OTA channel parsed: Network={ota_info['network']}, State={ota_info['state']}, City={ota_info['city']}, Callsign={ota_info['callsign']}")
        
        # Find matching network entries
        matching_networks = []
        for network_entry in networks_data:
            entry_callsign = self._parse_callsign(network_entry.get('callsign', ''))
            entry_network = self._parse_network_affiliation(network_entry.get('network_affiliation', ''))
            entry_state = network_entry.get('community_served_state', '').upper()
            
            # Match on callsign and state
            if (entry_callsign == ota_info['callsign'] and 
                entry_state == ota_info['state'] and
                entry_network == ota_info['network']):
                matching_networks.append(network_entry)
                logger.info(f"  Found matching network entry: {entry_callsign} in {entry_state}")
        
        if not matching_networks:
            logger.info(f"  No matching network entries found in networks.json")
            return []
        
        # Now search for streams matching the exact callsign
        matching_streams = []
        callsign = ota_info['callsign']
        
        for stream in all_streams:
            stream_name = stream['name'].upper()
            
            # Use word boundary regex to ensure exact callsign match
            # Pattern: callsign must be a complete word (not part of a longer callsign)
            callsign_pattern = r'\b' + re.escape(callsign) + r'\b'
            
            if re.search(callsign_pattern, stream_name):
                # Additional validation: ensure it is the right network
                # The stream should contain the network name or be validated by context
                if ota_info['network'] in stream_name:
                    matching_streams.append(stream)
                    logger.info(f"  Found OTA stream match: {stream['name']}")
        
        return matching_streams

    def run(self, action, params, context):
        """Main plugin entry point"""
        LOGGER.info(f"Stream-Mapparr run called with action: {action}")
        
        try:
            settings = context.get("settings", {})
            logger = context.get("logger", LOGGER)
            
            action_map = {
                "load_process_channels": self.load_process_channels_action,
                "preview_changes": self.preview_changes_action,
                "add_streams_to_channels": self.add_streams_to_channels_action,
                "manage_channel_visibility": self.manage_channel_visibility_action,
            }
            
            if action not in action_map:
                return {
                    "status": "error",
                    "message": f"Unknown action: {action}",
                    "available_actions": list(action_map.keys())
                }
            
            return action_map[action](settings, logger)
                
        except Exception as e:
            LOGGER.error(f"Error in plugin run: {str(e)}")
            return {"status": "error", "message": str(e)}

    def load_process_channels_action(self, settings, logger):
        """Load and process channels from specified profile and groups."""
        try:
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            profile_name = settings.get("profile_name", "").strip()
            selected_groups_str = settings.get("selected_groups", "").strip()
            ignore_tags_str = settings.get("ignore_tags", "").strip()
            visible_channel_limit = int(settings.get("visible_channel_limit", 1))
            
            if not profile_name:
                return {"status": "error", "message": "Profile Name must be configured in the plugin settings."}
            
            if visible_channel_limit < 1:
                return {"status": "error", "message": "Visible Channel Limit must be at least 1."}
            
            # Parse ignore tags
            ignore_tags = []
            if ignore_tags_str:
                ignore_tags = [tag.strip() for tag in ignore_tags_str.split(',') if tag.strip()]
                logger.info(f"Ignore tags configured: {ignore_tags}")
            
            # Get all profiles to find the specified one
            logger.info("Fetching channel profiles...")
            profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger)
            
            target_profile = None
            for profile in profiles:
                if profile.get('name', '').lower() == profile_name.lower():
                    target_profile = profile
                    break
            
            if not target_profile:
                available_profiles = [p.get('name') for p in profiles if 'name' in p]
                return {
                    "status": "error",
                    "message": f"Profile '{profile_name}' not found. Available profiles: {', '.join(available_profiles)}"
                }
            
            profile_id = target_profile['id']
            logger.info(f"Found profile: {profile_name} (ID: {profile_id})")
            
            # Get all groups (handle pagination)
            logger.info("Fetching channel groups...")
            all_groups = []
            page = 1
            while True:
                api_groups = self._get_api_data(f"/api/channels/groups/?page={page}", token, settings, logger)
                
                # Handle both paginated and non-paginated responses
                if isinstance(api_groups, dict) and 'results' in api_groups:
                    all_groups.extend(api_groups['results'])
                    if not api_groups.get('next'):
                        break
                    page += 1
                elif isinstance(api_groups, list):
                    all_groups.extend(api_groups)
                    break
                else:
                    break
            
            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            logger.info(f"Loaded {len(group_name_to_id)} channel groups total")
            
            # Get channels - API does not filter by profile automatically
            logger.info(f"Fetching all channels...")
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger)
            logger.info(f"Retrieved {len(all_channels)} total channels")
            
            # Filter channels by profile membership
            # Use Django ORM to check profile membership
            channels_in_profile = []
            for channel in all_channels:
                channel_id = channel['id']
                # Check if this channel is enabled in the target profile
                is_in_profile = ChannelProfileMembership.objects.filter(
                    channel_id=channel_id,
                    channel_profile_id=profile_id,
                    enabled=True
                ).exists()
                
                if is_in_profile:
                    channels_in_profile.append(channel)
            
            logger.info(f"Found {len(channels_in_profile)} channels in profile '{profile_name}'")
            
            # Filter by groups if specified
            if selected_groups_str:
                logger.info(f"Filtering by groups: {selected_groups_str}")
                selected_groups = [g.strip() for g in selected_groups_str.split(',') if g.strip()]
                valid_group_ids = [group_name_to_id[name] for name in selected_groups if name in group_name_to_id]
                
                if not valid_group_ids:
                    available_groups = sorted(list(group_name_to_id.keys()))
                    return {
                        "status": "error",
                        "message": f"None of the specified groups were found: {', '.join(selected_groups)}\n\nAvailable groups ({len(available_groups)} total):\n" + "\n".join(f"  • {g}" for g in available_groups)
                    }
                
                # Filter channels by channel_group_id field
                filtered_channels = []
                for ch in channels_in_profile:
                    if ch.get('channel_group_id') in valid_group_ids:
                        filtered_channels.append(ch)
                
                channels_in_profile = filtered_channels
                logger.info(f"Filtered to {len(channels_in_profile)} channels in groups: {', '.join(selected_groups)}")
                group_filter_info = f" in groups: {', '.join(selected_groups)}"
            else:
                selected_groups = []
                group_filter_info = " (all groups)"
            
            if not channels_in_profile:
                return {
                    "status": "error",
                    "message": f"No channels found in profile '{profile_name}'{group_filter_info}"
                }
            
            channels_to_process = channels_in_profile
            logger.info(f"Processing {len(channels_to_process)} channels (including those with existing streams)")
            
            # Get all streams - DO NOT filter by group, get all streams (handle pagination)
            logger.info("Fetching all streams from all groups...")
            all_streams_data = []
            page = 1
            max_pages = 100

            while page <= max_pages:
                endpoint = f"/api/channels/streams/?page={page}&page_size=100"
                streams_response = self._get_api_data(endpoint, token, settings, logger)
                
                # Handle both paginated and non-paginated responses
                if isinstance(streams_response, dict) and 'results' in streams_response:
                    results = streams_response['results']
                    all_streams_data.extend(results)
                    logger.info(f"Fetched page {page}: {len(results)} streams (total so far: {len(all_streams_data)})")
                    
                    # Stop if this page had fewer results than page_size (last page)
                    if len(results) < 100:
                        logger.info("Reached last page of streams")
                        break
                    
                    page += 1
                elif isinstance(streams_response, list):
                    # List response - could still be paginated
                    all_streams_data.extend(streams_response)
                    logger.info(f"Fetched page {page}: {len(streams_response)} streams (total so far: {len(all_streams_data)})")
                    
                    # If we got exactly 100 results, there might be more pages
                    if len(streams_response) == 100:
                        page += 1
                    else:
                        logger.info("Reached last page of streams")
                        break
                else:
                    logger.warning("Unexpected streams response format")
                    break

            logger.info(f"Retrieved {len(all_streams_data)} total streams from all groups")
            
            # Debug: Show sample stream names
            if all_streams_data:
                sample_stream_names = [s.get('name', 'N/A') for s in all_streams_data[:10]]
                logger.info(f"Sample stream names: {sample_stream_names}")
            
            # Store loaded data including ignore tags and visible channel limit
            self.loaded_channels = channels_to_process
            self.loaded_streams = all_streams_data
            
            # Save to file
            processed_data = {
                "loaded_at": datetime.now().isoformat(),
                "profile_name": profile_name,
                "profile_id": profile_id,
                "selected_groups": selected_groups,
                "ignore_tags": ignore_tags,
                "visible_channel_limit": visible_channel_limit,
                "channels": channels_to_process,
                "streams": all_streams_data
            }
            
            with open(self.processed_data_file, 'w') as f:
                json.dump(processed_data, f, indent=2)
            
            logger.info("Channel and stream data loaded and saved successfully")
            
            return {
                "status": "success",
                "message": f"Successfully loaded {len(channels_to_process)} channels from profile '{profile_name}'{group_filter_info}\n\nFound {len(all_streams_data)} streams available for matching.\n\nVisible channel limit set to: {visible_channel_limit}\n\nYou can now run 'Preview Changes' or 'Add Streams to Channels'."
            }
            
        except Exception as e:
            logger.error(f"Error loading channels: {str(e)}")
            return {"status": "error", "message": f"Error loading channels: {str(e)}"}

    def preview_changes_action(self, settings, logger):
        """Preview which streams will be added to channels without making changes."""
        if not os.path.exists(self.processed_data_file):
            return {
                "status": "error",
                "message": "No processed data found. Please run 'Load/Process Channels' first."
            }
        
        try:
            # Load known channel names for precise matching
            known_channels = self._load_channel_list(logger)
            networks_data = self._load_networks_data(logger)
            
            # Load processed data
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)
            
            channels = processed_data.get('channels', [])
            streams = processed_data.get('streams', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', 1)
            
            if not channels:
                return {"status": "error", "message": "No channels found in processed data."}
            
            logger.info(f"Previewing changes for {len(channels)} channels with {len(streams)} available streams")
            logger.info(f"Visible channel limit: {visible_channel_limit}")
            
            # Group channels by their cleaned name for matching
            channel_groups = {}
            ignore_tags = processed_data.get('ignore_tags', [])
            
            for channel in channels:
                # Use ORIGINAL name for OTA channels, cleaned name for others
                if self._is_ota_channel(channel['name']):
                    # For OTA channels, group by callsign
                    ota_info = self._extract_ota_info(channel['name'])
                    if ota_info:
                        group_key = f"OTA_{ota_info['callsign']}"
                    else:
                        group_key = self._clean_channel_name(channel['name'], ignore_tags)
                else:
                    group_key = self._clean_channel_name(channel['name'], ignore_tags)
                
                if group_key not in channel_groups:
                    channel_groups[group_key] = []
                channel_groups[group_key].append(channel)
            
            # Match streams to channel groups
            all_matches = []
            total_channels_with_matches = 0
            total_channels_without_matches = 0
            total_channels_to_update = 0
            
            for group_key, group_channels in channel_groups.items():
                logger.info(f"Processing channel group: {group_key} ({len(group_channels)} channels)")
                
                # Sort channels in this group by priority
                sorted_channels = self._sort_channels_by_priority(group_channels)
                
                # Match streams for this channel group (using first channel as representative)
                matched_streams = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, known_channels, networks_data
                )
                
                # Determine which channels will be updated based on limit
                channels_to_update = sorted_channels[:visible_channel_limit]
                channels_not_updated = sorted_channels[visible_channel_limit:]
                
                # Add match info for channels that will be updated
                for channel in channels_to_update:
                    match_info = {
                        "channel_id": channel['id'],
                        "channel_name": channel['name'],
                        "channel_number": channel.get('channel_number'),
                        "matched_streams": len(matched_streams),
                        "stream_names": [s['name'] for s in matched_streams],
                        "stream_ids": [s['id'] for s in matched_streams],
                        "will_update": True
                    }
                    all_matches.append(match_info)
                    
                    if matched_streams:
                        total_channels_with_matches += 1
                        total_channels_to_update += 1
                    else:
                        total_channels_without_matches += 1
                
                # Add match info for channels that will NOT be updated
                for channel in channels_not_updated:
                    match_info = {
                        "channel_id": channel['id'],
                        "channel_name": channel['name'],
                        "channel_number": channel.get('channel_number'),
                        "matched_streams": len(matched_streams),
                        "stream_names": [s['name'] for s in matched_streams],
                        "stream_ids": [s['id'] for s in matched_streams],
                        "will_update": False
                    }
                    all_matches.append(match_info)
                    
                    if matched_streams:
                        total_channels_with_matches += 1
                    else:
                        total_channels_without_matches += 1
            
            # Generate CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"stream_mapparr_preview_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'channel_id',
                    'channel_name',
                    'channel_number',
                    'matched_streams_count',
                    'stream_names',
                    'stream_ids',
                    'will_update'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for match in all_matches:
                    writer.writerow({
                        'channel_id': match['channel_id'],
                        'channel_name': match['channel_name'],
                        'channel_number': match['channel_number'],
                        'matched_streams_count': match['matched_streams'],
                        'stream_names': ' | '.join(match['stream_names']),
                        'stream_ids': ', '.join(map(str, match['stream_ids'])),
                        'will_update': 'Yes' if match['will_update'] else 'No'
                    })
            
            logger.info(f"Preview CSV exported to {filepath}")
            
            # Create summary message
            message_parts = [
                "Preview completed:",
                f"• Total channels: {len(channels)}",
                f"• Channels with matches: {total_channels_with_matches}",
                f"• Channels without matches: {total_channels_without_matches}",
                f"• Channels that will be updated: {total_channels_to_update}",
                f"• Visible channel limit: {visible_channel_limit}",
                "",
                f"Preview exported to: {filepath}",
                "",
                "Sample matches (✓ = will update):"
            ]
            
            # Show first 10 channels
            for match in all_matches[:10]:
                update_marker = "✓" if match['will_update'] else "✗"
                if match['matched_streams'] > 0:
                    stream_preview = ', '.join(match['stream_names'][:3])
                    if match['matched_streams'] > 3:
                        stream_preview += f" ... (+{match['matched_streams'] - 3} more)"
                    message_parts.append(f"{update_marker} {match['channel_name']}: {stream_preview}")
                else:
                    message_parts.append(f"{update_marker} {match['channel_name']}: No matches found")
            
            if len(all_matches) > 10:
                message_parts.append(f"... and {len(all_matches) - 10} more channels")
            
            message_parts.append("")
            message_parts.append("Use 'Add Streams to Channels' to apply these changes.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error previewing changes: {str(e)}")
            return {"status": "error", "message": f"Error previewing changes: {str(e)}"}

    def add_streams_to_channels_action(self, settings, logger):
        """Add matching streams to channels and replace existing assignments."""
        if not os.path.exists(self.processed_data_file):
            return {
                "status": "error",
                "message": "No processed data found. Please run 'Load/Process Channels' first."
            }
        
        try:
            # Get API token
            token, error = self._get_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Load known channel names for precise matching
            known_channels = self._load_channel_list(logger)
            networks_data = self._load_networks_data(logger)
            
            # Load processed data
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)
            
            channels = processed_data.get('channels', [])
            streams = processed_data.get('streams', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', 1)
            profile_id = processed_data.get('profile_id')
            
            if not channels:
                return {"status": "error", "message": "No channels found in processed data."}
            
            if not profile_id:
                return {"status": "error", "message": "Profile ID not found in processed data."}
            
            logger.info(f"Adding streams to channels with visible limit: {visible_channel_limit}")
            
            # Group channels by their cleaned name for matching
            channel_groups = {}
            ignore_tags = processed_data.get('ignore_tags', [])
            
            for channel in channels:
                # Use ORIGINAL name for OTA channels, cleaned name for others
                if self._is_ota_channel(channel['name']):
                    # For OTA channels, group by callsign
                    ota_info = self._extract_ota_info(channel['name'])
                    if ota_info:
                        group_key = f"OTA_{ota_info['callsign']}"
                    else:
                        group_key = self._clean_channel_name(channel['name'], ignore_tags)
                else:
                    group_key = self._clean_channel_name(channel['name'], ignore_tags)
                
                if group_key not in channel_groups:
                    channel_groups[group_key] = []
                channel_groups[group_key].append(channel)
            
            # Match streams to channel groups and update
            all_matches = []
            channels_updated = 0
            channels_skipped = 0
            channels_to_enable = []  # Track channels that should be enabled in the profile
            
            for group_key, group_channels in channel_groups.items():
                logger.info(f"Processing channel group: {group_key} ({len(group_channels)} channels)")
                
                # Sort channels in this group by priority
                sorted_channels = self._sort_channels_by_priority(group_channels)
                
                # Match streams for this channel group (using first channel as representative)
                matched_streams = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, known_channels, networks_data
                )
                
                # Determine which channels will be updated based on limit
                channels_to_update = sorted_channels[:visible_channel_limit]
                channels_not_updated = sorted_channels[visible_channel_limit:]
                
                # Update only the channels within the visible limit
                if matched_streams:
                    stream_ids = [s['id'] for s in matched_streams]
                    
                    for channel in channels_to_update:
                        try:
                            payload = {
                                'id': channel['id'],
                                'streams': stream_ids
                            }
                            
                            logger.info(f"Updating channel {channel['name']} (ID: {channel['id']}) with {len(stream_ids)} streams")
                            self._patch_api_data(
                                f"/api/channels/channels/{channel['id']}/",
                                token,
                                payload,
                                settings,
                                logger
                            )
                            
                            # Add to list of channels to enable in profile
                            channels_to_enable.append(channel['id'])
                            
                            match_info = {
                                "channel_id": channel['id'],
                                "channel_name": channel['name'],
                                "channel_number": channel.get('channel_number'),
                                "matched_streams": len(matched_streams),
                                "stream_names": [s['name'] for s in matched_streams],
                                "stream_ids": stream_ids,
                                "will_update": True,
                                "status": "Updated"
                            }
                            all_matches.append(match_info)
                            channels_updated += 1
                            
                        except Exception as e:
                            logger.error(f"Failed to update channel {channel['name']} (ID: {channel['id']}): {e}")
                            match_info = {
                                "channel_id": channel['id'],
                                "channel_name": channel['name'],
                                "channel_number": channel.get('channel_number'),
                                "matched_streams": len(matched_streams),
                                "stream_names": [s['name'] for s in matched_streams],
                                "stream_ids": stream_ids,
                                "will_update": True,
                                "status": f"Error: {str(e)}"
                            }
                            all_matches.append(match_info)
                            channels_skipped += 1
                    
                    # Add match info for channels NOT updated (over the limit)
                    for channel in channels_not_updated:
                        match_info = {
                            "channel_id": channel['id'],
                            "channel_name": channel['name'],
                            "channel_number": channel.get('channel_number'),
                            "matched_streams": len(matched_streams),
                            "stream_names": [s['name'] for s in matched_streams],
                            "stream_ids": stream_ids,
                            "will_update": False,
                            "status": "Skipped (over limit)"
                        }
                        all_matches.append(match_info)
                        channels_skipped += 1
                else:
                    # No matches found for this group
                    for channel in sorted_channels:
                        match_info = {
                            "channel_id": channel['id'],
                            "channel_name": channel['name'],
                            "channel_number": channel.get('channel_number'),
                            "matched_streams": 0,
                            "stream_names": [],
                            "stream_ids": [],
                            "will_update": False,
                            "status": "No matches"
                        }
                        all_matches.append(match_info)
                        channels_skipped += 1
            
            # Enable channels in the profile that received streams
            channels_enabled = 0
            if channels_to_enable:
                logger.info(f"Enabling {len(channels_to_enable)} channels in profile (ID: {profile_id})")
                try:
                    # Build bulk update payload
                    bulk_payload = [
                        {"channel_id": channel_id, "enabled": True}
                        for channel_id in channels_to_enable
                    ]
                    
                    # Use bulk update endpoint
                    self._patch_api_data(
                        f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
                        token,
                        bulk_payload,
                        settings,
                        logger
                    )
                    channels_enabled = len(channels_to_enable)
                    logger.info(f"Successfully enabled {channels_enabled} channels in profile")
                    
                except Exception as e:
                    logger.error(f"Failed to bulk enable channels in profile: {e}")
                    logger.info("Attempting to enable channels individually...")
                    
                    # Fallback: enable channels one by one
                    for channel_id in channels_to_enable:
                        try:
                            self._patch_api_data(
                                f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
                                token,
                                {"enabled": True},
                                settings,
                                logger
                            )
                            channels_enabled += 1
                        except Exception as e2:
                            logger.error(f"Failed to enable channel {channel_id}: {e2}")
            
            # Trigger M3U refresh
            if channels_updated > 0:
                self._trigger_m3u_refresh(token, settings, logger)
            
            # Generate results CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"stream_mapparr_results_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'channel_id',
                    'channel_name',
                    'channel_number',
                    'matched_streams_count',
                    'stream_names',
                    'stream_ids',
                    'will_update',
                    'status'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for match in all_matches:
                    writer.writerow({
                        'channel_id': match['channel_id'],
                        'channel_name': match['channel_name'],
                        'channel_number': match['channel_number'],
                        'matched_streams_count': match['matched_streams'],
                        'stream_names': ' | '.join(match['stream_names']),
                        'stream_ids': ', '.join(map(str, match['stream_ids'])),
                        'will_update': 'Yes' if match['will_update'] else 'No',
                        'status': match['status']
                    })
            
            logger.info(f"Results CSV exported to {filepath}")
            
            # Create summary message
            message_parts = [
                "Stream addition completed:",
                f"• Total channels processed: {len(channels)}",
                f"• Channels updated: {channels_updated}",
                f"• Channels enabled in profile: {channels_enabled}",
                f"• Channels skipped: {channels_skipped}",
                f"• Visible channel limit: {visible_channel_limit}",
                "",
                f"Results exported to: {filepath}",
                "",
                "Sample updates (✓ = updated):"
            ]
            
            # Show first 10 channels
            shown_count = 0
            for match in all_matches:
                if shown_count >= 10:
                    break
                
                update_marker = "✓" if match['will_update'] else "✗"
                if match['matched_streams'] > 0:
                    stream_preview = ', '.join(match['stream_names'][:3])
                    if match['matched_streams'] > 3:
                        stream_preview += f" ... (+{match['matched_streams'] - 3} more)"
                    message_parts.append(f"{update_marker} {match['channel_name']}: {match['status']}")
                    if match['will_update']:
                        message_parts.append(f"  Streams: {stream_preview}")
                else:
                    message_parts.append(f"{update_marker} {match['channel_name']}: {match['status']}")
                
                shown_count += 1
            
            if len(all_matches) > 10:
                message_parts.append(f"... and {len(all_matches) - 10} more channels")
            
            message_parts.append("")
            message_parts.append("GUI refresh triggered - changes should be visible in the interface shortly.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error adding streams to channels: {str(e)}")
            return {"status": "error", "message": f"Error adding streams to channels: {str(e)}"}

    def manage_channel_visibility_action(self, settings, logger):
            """Disable all channels in profile, then enable only channels with 0 or 1 stream that are not attached to other channels or duplicates."""
            if not os.path.exists(self.processed_data_file):
                return {
                    "status": "error",
                    "message": "No processed data found. Please run 'Load/Process Channels' first."
                }
            
            try:
                # Get API token
                token, error = self._get_api_token(settings, logger)
                if error:
                    return {"status": "error", "message": error}
                
                # Load processed data
                with open(self.processed_data_file, 'r') as f:
                    processed_data = json.load(f)
                
                channels = processed_data.get('channels', [])
                profile_id = processed_data.get('profile_id')
                ignore_tags = processed_data.get('ignore_tags', [])
                
                if not channels:
                    return {"status": "error", "message": "No channels found in processed data."}
                
                if not profile_id:
                    return {"status": "error", "message": "Profile ID not found in processed data."}
                
                logger.info(f"Managing visibility for {len(channels)} channels in profile (ID: {profile_id})")
                
                # Step 1: Disable ALL channels in the profile
                logger.info("Step 1: Disabling all channels in profile...")
                try:
                    bulk_disable_payload = [
                        {"channel_id": ch['id'], "enabled": False}
                        for ch in channels
                    ]
                    
                    self._patch_api_data(
                        f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
                        token,
                        bulk_disable_payload,
                        settings,
                        logger
                    )
                    logger.info(f"Successfully disabled {len(channels)} channels")
                except Exception as e:
                    logger.error(f"Failed to bulk disable channels: {e}")
                    logger.info("Attempting to disable channels individually...")
                    
                    # Fallback: disable one by one
                    disabled_count = 0
                    for channel in channels:
                        try:
                            self._patch_api_data(
                                f"/api/channels/profiles/{profile_id}/channels/{channel['id']}/",
                                token,
                                {"enabled": False},
                                settings,
                                logger
                            )
                            disabled_count += 1
                        except Exception as e2:
                            logger.error(f"Failed to disable channel {channel['id']}: {e2}")
                    
                    logger.info(f"Disabled {disabled_count} channels individually")
                
                # Step 2: Group channels by callsign and get stream counts
                logger.info("Step 2: Grouping channels and checking stream assignments...")
                
                # Group channels by callsign (for OTA) or cleaned name (for regular)
                channel_groups = {}
                channels_attached_to_others = set()
                
                for channel in channels:
                    channel_id = channel['id']
                    channel_name = channel['name']
                    
                    # Determine group key
                    if self._is_ota_channel(channel_name):
                        ota_info = self._extract_ota_info(channel_name)
                        if ota_info:
                            group_key = f"OTA_{ota_info['callsign']}"
                        else:
                            group_key = self._clean_channel_name(channel_name, ignore_tags)
                    else:
                        group_key = self._clean_channel_name(channel_name, ignore_tags)
                    
                    if group_key not in channel_groups:
                        channel_groups[group_key] = []
                    
                    # Get channel data
                    try:
                        channel_data = self._get_api_data(
                            f"/api/channels/channels/{channel_id}/",
                            token,
                            settings,
                            logger
                        )
                        
                        # Check if attached to another channel
                        if channel_data.get('channel') is not None:
                            channels_attached_to_others.add(channel_id)
                            logger.info(f"  Channel {channel_name} is attached to another channel")
                        
                        stream_count = len(channel_data.get('streams', []))
                        
                        channel_groups[group_key].append({
                            'id': channel_id,
                            'name': channel_name,
                            'stream_count': stream_count,
                            'channel_number': channel.get('channel_number'),
                            'attached': channel_id in channels_attached_to_others
                        })
                        
                    except Exception as e:
                        logger.error(f"Failed to get data for channel {channel_id}: {e}")
                
                # Step 3: Determine which channels to enable
                logger.info("Step 3: Determining which channels to enable...")
                channels_to_enable = []
                channel_stream_counts = {}
                
                for group_key, group_channels in channel_groups.items():
                    # Sort channels by priority (same logic as other actions)
                    sorted_group = self._sort_channels_by_priority([
                        {'id': ch['id'], 'name': ch['name'], 'channel_number': ch['channel_number']}
                        for ch in group_channels
                    ])
                    
                    # Find channels with 0-1 streams (not attached)
                    eligible_channels = [
                        ch for ch in group_channels 
                        if (ch['stream_count'] == 0 or ch['stream_count'] == 1) and not ch['attached']
                    ]
                    
                    # If there are eligible channels, enable only the highest priority one
                    enabled_in_group = False
                    
                    for ch in group_channels:
                        channel_id = ch['id']
                        channel_name = ch['name']
                        stream_count = ch['stream_count']
                        is_attached = ch['attached']
                        
                        # Determine reason for enabling/disabling
                        if is_attached:
                            reason = 'Attached to another channel'
                            should_enable = False
                        elif stream_count >= 2:
                            reason = f'{stream_count} streams (too many)'
                            should_enable = False
                        elif not enabled_in_group and (stream_count == 0 or stream_count == 1):
                            # This is the highest priority channel with 0-1 streams
                            reason = f'{stream_count} stream{"" if stream_count == 1 else "s"}'
                            should_enable = True
                            enabled_in_group = True
                        else:
                            # Another channel in this group is already enabled
                            reason = 'Duplicate - higher priority channel in group already enabled'
                            should_enable = False
                        
                        channel_stream_counts[channel_id] = {
                            'name': channel_name,
                            'stream_count': stream_count,
                            'reason': reason
                        }
                        
                        if should_enable:
                            channels_to_enable.append(channel_id)
                            logger.info(f"  Will enable: {channel_name} ({reason})")
                        else:
                            logger.info(f"  Will keep disabled: {channel_name} ({reason})")
                
                # Step 4: Enable selected channels
                logger.info(f"Step 4: Enabling {len(channels_to_enable)} channels...")
                channels_enabled = 0
                
                if channels_to_enable:
                    try:
                        bulk_enable_payload = [
                            {"channel_id": channel_id, "enabled": True}
                            for channel_id in channels_to_enable
                        ]
                        
                        self._patch_api_data(
                            f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
                            token,
                            bulk_enable_payload,
                            settings,
                            logger
                        )
                        channels_enabled = len(channels_to_enable)
                        logger.info(f"Successfully enabled {channels_enabled} channels")
                        
                    except Exception as e:
                        logger.error(f"Failed to bulk enable channels: {e}")
                        logger.info("Attempting to enable channels individually...")
                        
                        # Fallback: enable one by one
                        for channel_id in channels_to_enable:
                            try:
                                self._patch_api_data(
                                    f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
                                    token,
                                    {"enabled": True},
                                    settings,
                                    logger
                                )
                                channels_enabled += 1
                            except Exception as e2:
                                logger.error(f"Failed to enable channel {channel_id}: {e2}")
                
                # Trigger M3U refresh
                self._trigger_m3u_refresh(token, settings, logger)
                
                # Generate visibility report CSV
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"stream_mapparr_visibility_{timestamp}.csv"
                filepath = os.path.join("/data/exports", filename)
                
                os.makedirs("/data/exports", exist_ok=True)
                
                with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                    fieldnames = [
                        'channel_id',
                        'channel_name',
                        'stream_count',
                        'reason',
                        'enabled'
                    ]
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    
                    for channel_id, info in channel_stream_counts.items():
                        writer.writerow({
                            'channel_id': channel_id,
                            'channel_name': info['name'],
                            'stream_count': info.get('stream_count', 'N/A'),
                            'reason': info.get('reason', ''),
                            'enabled': 'Yes' if channel_id in channels_to_enable else 'No'
                        })
                
                logger.info(f"Visibility report exported to {filepath}")
                
                # Count channels by category
                channels_with_0_streams = sum(1 for info in channel_stream_counts.values() if info.get('stream_count') == 0)
                channels_with_1_stream = sum(1 for info in channel_stream_counts.values() if info.get('stream_count') == 1)
                channels_with_2plus_streams = sum(1 for info in channel_stream_counts.values() if isinstance(info.get('stream_count'), int) and info.get('stream_count') >= 2)
                channels_attached = len(channels_attached_to_others)
                channels_duplicates = len([info for info in channel_stream_counts.values() if 'Duplicate' in info.get('reason', '')])
                
                # Create summary message
                message_parts = [
                    "Channel visibility management completed:",
                    f"• Total channels processed: {len(channels)}",
                    f"• Channels disabled: {len(channels) - channels_enabled}",
                    f"• Channels enabled: {channels_enabled}",
                    "",
                    "Breakdown:",
                    f"• Enabled (0-1 streams): {channels_enabled} channels",
                    f"• Disabled (2+ streams): {channels_with_2plus_streams} channels",
                    f"• Disabled (duplicates): {channels_duplicates} channels",
                    f"• Disabled (attached): {channels_attached} channels",
                    "",
                    f"Visibility report exported to: {filepath}",
                    "",
                    "Sample enabled channels:"
                ]
                
                # Show first 10 enabled channels
                shown_count = 0
                for channel_id in channels_to_enable[:10]:
                    info = channel_stream_counts.get(channel_id)
                    if info:
                        message_parts.append(f"✓ {info['name']}: {info.get('reason', 'N/A')}")
                        shown_count += 1
                
                if len(channels_to_enable) > 10:
                    message_parts.append(f"... and {len(channels_to_enable) - 10} more enabled channels")
                
                message_parts.append("")
                message_parts.append("GUI refresh triggered - changes should be visible in the interface shortly.")
                
                return {
                    "status": "success",
                    "message": "\n".join(message_parts)
                }
                
            except Exception as e:
                logger.error(f"Error managing channel visibility: {str(e)}")
                return {"status": "error", "message": f"Error managing channel visibility: {str(e)}"}


# Export fields and actions for Dispatcharr plugin system
fields = Plugin.fields
actions = Plugin.actions

# Additional exports for Dispatcharr plugin system compatibility
plugin = Plugin()
plugin_instance = Plugin()

# Alternative export names in case Dispatcharr looks for these
stream_mapparr = Plugin()
STREAM_MAPPARR = Plugin()