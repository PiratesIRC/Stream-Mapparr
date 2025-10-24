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
    version = "0.3"
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
        {
            "id": "clear_csv_exports",
            "label": "Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin",
            "confirm": {
                "required": True,
                "title": "Clear CSV Exports?",
                "message": "This will delete all CSV export files created by this plugin. Continue?"
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

    def _trigger_frontend_refresh(self, settings, logger):
        """Trigger frontend channel list refresh via WebSocket"""
        try:
            from channels.layers import get_channel_layer
            from asgiref.sync import async_to_sync
            
            channel_layer = get_channel_layer()
            if channel_layer:
                # Send WebSocket message to trigger frontend refresh
                async_to_sync(channel_layer.group_send)(
                    "dispatcharr_updates",
                    {
                        "type": "channels.updated",
                        "message": "Channel visibility updated by Event Channel Managarr"
                    }
                )
                logger.info("Frontend refresh triggered via WebSocket")
                return True
        except Exception as e:
            logger.warning(f"Could not trigger frontend refresh: {e}")
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
                    rf'\[{re.escape(quality_clean)}\]',
                    rf'\({re.escape(quality_clean)}\)',
                    rf'\b{re.escape(quality_clean)}\b'
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
        
        cleaned_channel_name = self._clean_channel_name(channel_name, ignore_tags)
        #logger.info(f"  Cleaned channel name for matching: {cleaned_channel_name}")
        if "24/7" in channel_name.lower():
            logger.info(f"  Cleaned channel name for matching: {cleaned_channel_name}")
                
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
                    
                    cleaned_stream_names = [self._clean_channel_name(s['name'], ignore_tags) for s in sorted_streams]
                    match_reason = "Callsign match"
                    
                    return sorted_streams, cleaned_channel_name, cleaned_stream_names, match_reason
                else:
                    logger.info(f"  No streams found with callsign {callsign}")
                    # Try networks.json fallback
                    logger.info(f"  Trying networks.json OTA matching as fallback")
                    matched_streams = self._match_ota_streams(channel_name, all_streams, networks_data, logger)
                    
                    if matched_streams:
                        sorted_streams = self._sort_streams_by_quality(matched_streams)
                        logger.info(f"  Sorted {len(sorted_streams)} OTA streams by quality")
                        
                        cleaned_stream_names = [self._clean_channel_name(s['name'], ignore_tags) for s in sorted_streams]
                        match_reason = "OTA networks.json match"
                        
                        return sorted_streams, cleaned_channel_name, cleaned_stream_names, match_reason
            else:
                logger.info(f"  Could not extract OTA info from: {channel_name}")
        
        # SECOND: Regular channel matching logic (only if not OTA or OTA matching failed)
        matching_streams = []
        match_reason = "No match"
        
        # Debug: show first few stream names to verify we have streams
        if not all_streams:
            logger.warning("No streams available for matching!")
            return [], cleaned_channel_name, [], "No streams available"
        
        # Check for exact match in known channels list
        if cleaned_channel_name in known_channels:
            logger.info(f"Found exact match for '{cleaned_channel_name}' in known channels list")
            
            # Look for streams that match this channel name exactly
            for stream in all_streams:
                cleaned_stream_name = self._clean_channel_name(stream['name'], ignore_tags)
                
                if cleaned_stream_name.lower() == cleaned_channel_name.lower():
                    matching_streams.append(stream)
            
            if matching_streams:
                sorted_streams = self._sort_streams_by_quality(matching_streams)
                logger.info(f"  Found {len(sorted_streams)} streams matching exact known channel name")
                
                cleaned_stream_names = [self._clean_channel_name(s['name'], ignore_tags) for s in sorted_streams]
                match_reason = "Exact match (known channels)"
                
                return sorted_streams, cleaned_channel_name, cleaned_stream_names, match_reason
        
        # Fallback to fuzzy matching
        for stream in all_streams:
            cleaned_stream_name = self._clean_channel_name(stream['name'], ignore_tags)
            
            # Simple case-insensitive substring matching
            if cleaned_channel_name.lower() in cleaned_stream_name.lower() or cleaned_stream_name.lower() in cleaned_channel_name.lower():
                matching_streams.append(stream)
        
        if matching_streams:
            sorted_streams = self._sort_streams_by_quality(matching_streams)
            logger.info(f"  Found {len(sorted_streams)} streams matching via fuzzy match")
            
            cleaned_stream_names = [self._clean_channel_name(s['name'], ignore_tags) for s in sorted_streams]
            match_reason = "Fuzzy match"
            
            return sorted_streams, cleaned_channel_name, cleaned_stream_names, match_reason
        
        # No match found
        return [], cleaned_channel_name, [], "No match"

    def _load_networks_data(self, logger):
        """Load networks.json file for OTA channel matching."""
        networks_file = os.path.join(os.path.dirname(__file__), 'networks.json')
        networks_data = []
        
        try:
            if os.path.exists(networks_file):
                with open(networks_file, 'r', encoding='utf-8') as f:
                    networks_data = json.load(f)
                logger.info(f"Loaded {len(networks_data)} networks from networks.json")
            else:
                logger.warning(f"networks.json not found at {networks_file}")
        except Exception as e:
            logger.error(f"Error loading networks.json: {e}")
        
        return networks_data

    def _match_ota_streams(self, channel_name, all_streams, networks_data, logger):
        """Match OTA channel using networks.json data."""
        if not networks_data:
            return []
        
        ota_info = self._extract_ota_info(channel_name)
        if not ota_info:
            return []
        
        network = ota_info['network']
        state = ota_info['state']
        city = ota_info['city']
        
        logger.info(f"  Searching networks.json for: {network} {state} {city}")
        
        # Find matching network entry
        matching_entry = None
        for entry in networks_data:
            if (entry.get('network', '').upper() == network and 
                entry.get('state', '').upper() == state and
                entry.get('city', '').upper() == city):
                matching_entry = entry
                break
        
        if not matching_entry:
            logger.info(f"  No networks.json entry found for {network} {state} {city}")
            return []
        
        # Get stream names from the entry
        stream_names = matching_entry.get('stream_names', [])
        if not stream_names:
            logger.info(f"  No stream names in networks.json entry")
            return []
        
        logger.info(f"  Found {len(stream_names)} stream names in networks.json: {stream_names}")
        
        # Find streams matching these names
        matching_streams = []
        for stream in all_streams:
            stream_name = stream['name']
            for target_name in stream_names:
                # Case-insensitive substring match
                if target_name.lower() in stream_name.lower():
                    matching_streams.append(stream)
                    logger.info(f"  Matched stream: {stream_name}")
                    break
        
        return matching_streams

    def run(self, action, settings, context=None):
        """Execute plugin action."""
        try:
            logger = LOGGER
            
            # If settings is empty but context has settings, use context settings
            if context and isinstance(context, dict) and not settings:
                if 'settings' in context:
                    settings = context['settings']
            
            action_map = {
                "load_process_channels": self.load_process_channels_action,
                "preview_changes": self.preview_changes_action,
                "add_streams_to_channels": self.add_streams_to_channels_action,
                "manage_channel_visibility": self.manage_channel_visibility_action,
                "clear_csv_exports": self.clear_csv_exports_action,
            }
            
            if action not in action_map:
                return {"status": "error", "message": f"Unknown action: {action}"}
            
            return action_map[action](settings, logger)
                
        except Exception as e:
            LOGGER.error(f"Error in plugin run: {str(e)}")
            import traceback
            LOGGER.error(traceback.format_exc())
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
            
            # Get all streams - DO NOT filter by group, get all streams (handle pagination with NO LIMIT)
            logger.info("Fetching all streams from all groups (unlimited)...")
            all_streams_data = []
            page = 1

            while True:
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
                matched_streams, cleaned_channel_name, cleaned_stream_names, match_reason = self._match_streams_to_channel(
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
                        "channel_name_cleaned": cleaned_channel_name,
                        "channel_number": channel.get('channel_number'),
                        "matched_streams": len(matched_streams),
                        "stream_names": [s['name'] for s in matched_streams],
                        "stream_names_cleaned": cleaned_stream_names,
                        "match_reason": match_reason,
                        "will_update": True
                    }
                    all_matches.append(match_info)
                    
                    if matched_streams:
                        total_channels_with_matches += 1
                    else:
                        total_channels_without_matches += 1
                    total_channels_to_update += 1
                
                # Add match info for channels that will NOT be updated (exceeds limit)
                for channel in channels_not_updated:
                    match_info = {
                        "channel_id": channel['id'],
                        "channel_name": channel['name'],
                        "channel_name_cleaned": cleaned_channel_name,
                        "channel_number": channel.get('channel_number'),
                        "matched_streams": len(matched_streams),
                        "stream_names": [s['name'] for s in matched_streams],
                        "stream_names_cleaned": cleaned_stream_names,
                        "match_reason": f"Skipped (exceeds limit of {visible_channel_limit})",
                        "will_update": False
                    }
                    all_matches.append(match_info)
            
            # Export to CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"stream_mapparr_preview_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'will_update',
                    'channel_id',
                    'channel_name',
                    'channel_name_cleaned',
                    'channel_number',
                    'matched_streams',
                    'match_reason',
                    'stream_names'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for match in all_matches:
                    writer.writerow({
                        'will_update': 'Yes' if match['will_update'] else 'No',
                        'channel_id': match['channel_id'],
                        'channel_name': match['channel_name'],
                        'channel_name_cleaned': match['channel_name_cleaned'],
                        'channel_number': match.get('channel_number', 'N/A'),
                        'matched_streams': match['matched_streams'],
                        'match_reason': match['match_reason'],
                        'stream_names': '; '.join(match['stream_names'][:5])  # Show first 5
                    })
            
            logger.info(f"Preview exported to {filepath}")
            
            # Calculate summary
            channels_skipped = len([m for m in all_matches if not m['will_update']])
            
            message_parts = [
                f"Preview completed for {len(channels)} channels:",
                f"• Channels that will be updated: {total_channels_to_update}",
                f"• Channels skipped (exceeds limit): {channels_skipped}",
                f"• Channels with matches: {total_channels_with_matches}",
                f"• Channels without matches: {total_channels_without_matches}",
                "",
                f"Preview report exported to: {filepath}",
                "",
                "Sample channels to update (first 10):"
            ]
            
            # Show first 10 channels that will be updated
            shown = 0
            for match in all_matches:
                if match['will_update'] and shown < 10:
                    stream_summary = f"{match['matched_streams']} stream(s)" if match['matched_streams'] > 0 else "No streams"
                    message_parts.append(f"• {match['channel_name']}: {stream_summary}")
                    shown += 1
            
            if total_channels_to_update > 10:
                message_parts.append(f"... and {total_channels_to_update - 10} more channels")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error previewing changes: {str(e)}")
            return {"status": "error", "message": f"Error previewing changes: {str(e)}"}

    def add_streams_to_channels_action(self, settings, logger):
        """Add matching streams to channels and replace existing stream assignments."""
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
            
            # Load known channel names and networks data
            known_channels = self._load_channel_list(logger)
            networks_data = self._load_networks_data(logger)
            
            # Load processed data
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)
            
            profile_id = processed_data.get('profile_id')
            channels = processed_data.get('channels', [])
            streams = processed_data.get('streams', [])
            ignore_tags = processed_data.get('ignore_tags', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', 1)
            
            if not channels:
                return {"status": "error", "message": "No channels found in processed data."}
            
            logger.info(f"Adding streams to {len(channels)} channels")
            logger.info(f"Visible channel limit: {visible_channel_limit}")
            
            # Group channels by their cleaned name
            channel_groups = {}
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
            
            # Process each channel group
            channels_updated = 0
            channels_skipped = 0
            channels_with_matches = 0
            channels_without_matches = 0
            update_details = []
            
            for group_key, group_channels in channel_groups.items():
                logger.info(f"Processing channel group: {group_key} ({len(group_channels)} channels)")
                
                # Sort channels in this group by priority
                sorted_channels = self._sort_channels_by_priority(group_channels)
                
                # Match streams for this channel group
                matched_streams, cleaned_channel_name, cleaned_stream_names, match_reason = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, known_channels, networks_data
                )
                
                # Determine which channels to update based on limit
                channels_to_update = sorted_channels[:visible_channel_limit]
                channels_not_updated = sorted_channels[visible_channel_limit:]
                
                # Update only the channels within the limit
                for channel in channels_to_update:
                    channel_id = channel['id']
                    channel_name = channel['name']
                    
                    try:
                        if matched_streams:
                            # Get the best stream (first in sorted list)
                            best_stream = matched_streams[0]
                            stream_id = best_stream['id']
                            
                            # Check if channel already has this stream assigned
                            existing_stream = ChannelStream.objects.filter(
                                channel_id=channel_id
                            ).first()
                            
                            if existing_stream and existing_stream.stream_id == stream_id:
                                logger.info(f"  Channel '{channel_name}' already has stream '{best_stream['name']}' - skipping")
                                channels_skipped += 1
                                continue
                            
                            # Remove existing stream assignments
                            ChannelStream.objects.filter(channel_id=channel_id).delete()
                            
                            # Add new stream assignment
                            ChannelStream.objects.create(
                                channel_id=channel_id,
                                stream_id=stream_id
                            )
                            
                            channels_updated += 1
                            channels_with_matches += 1
                            
                            update_details.append({
                                'channel_name': channel_name,
                                'stream_name': best_stream['name'],
                                'matched_streams': len(matched_streams)
                            })
                            
                            logger.info(f"  Added stream '{best_stream['name']}' to channel '{channel_name}'")
                        else:
                            # No matches found - remove existing streams
                            existing_count = ChannelStream.objects.filter(channel_id=channel_id).count()
                            if existing_count > 0:
                                ChannelStream.objects.filter(channel_id=channel_id).delete()
                                logger.info(f"  Removed {existing_count} stream(s) from channel '{channel_name}' (no matches found)")
                            
                            channels_without_matches += 1
                            
                    except Exception as e:
                        logger.error(f"  Failed to update channel '{channel_name}': {e}")
                
                # Log skipped channels
                for channel in channels_not_updated:
                    logger.info(f"  Skipped channel '{channel['name']}' (exceeds limit of {visible_channel_limit})")
                    channels_skipped += 1
            
            # Trigger frontend refresh
            self._trigger_frontend_refresh(settings, logger)
            
            # Export detailed report
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"stream_mapparr_update_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = ['channel_name', 'stream_name', 'matched_streams']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for detail in update_details:
                    writer.writerow(detail)
            
            logger.info(f"Update report exported to {filepath}")
            
            # Create summary message
            message_parts = [
                f"Stream assignment completed:",
                f"• Channels updated: {channels_updated}",
                f"• Channels skipped (exceeds limit): {channels_skipped}",
                f"• Channels with matches: {channels_with_matches}",
                f"• Channels without matches: {channels_without_matches}",
                "",
                f"Update report exported to: {filepath}",
                "",
                "Sample updates (first 10):"
            ]
            
            # Show first 10 updates
            for detail in update_details[:10]:
                message_parts.append(f"• {detail['channel_name']}: {detail['stream_name']}")
            
            if len(update_details) > 10:
                message_parts.append(f"... and {len(update_details) - 10} more updates")
            
            message_parts.append("")
            message_parts.append("Frontend refresh triggered - changes should be visible in the interface shortly.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error adding streams to channels: {str(e)}")
            return {"status": "error", "message": f"Error adding streams to channels: {str(e)}"}

    def manage_channel_visibility_action(self, settings, logger):
        """Disable all channels, then enable only channels with 0 or 1 stream (excluding attached channels)."""
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
            
            profile_id = processed_data.get('profile_id')
            channels = processed_data.get('channels', [])
            ignore_tags = processed_data.get('ignore_tags', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', 1)
            
            if not channels:
                return {"status": "error", "message": "No channels found in processed data."}
            
            logger.info(f"Managing visibility for {len(channels)} channels")
            logger.info(f"Visible channel limit: {visible_channel_limit}")
            
            # Step 1: Get stream counts for all channels
            logger.info("Step 1: Counting streams for each channel...")
            channel_stream_counts = {}
            
            for channel in channels:
                channel_id = channel['id']
                stream_count = ChannelStream.objects.filter(channel_id=channel_id).count()
                channel_stream_counts[channel_id] = {
                    'name': channel['name'],
                    'stream_count': stream_count
                }
                logger.info(f"  Channel '{channel['name']}': {stream_count} stream(s)")
            
            # Step 2: Find channels that are attached to other channels
            logger.info("Step 2: Identifying attached channels...")
            channels_attached_to_others = set()
            
            for channel in channels:
                attached_channel_id = channel.get('attached_channel_id')
                if attached_channel_id:
                    channels_attached_to_others.add(channel['id'])
                    logger.info(f"  Channel '{channel['name']}' is attached to another channel")
            
            # Step 3: Disable all channels first
            logger.info(f"Step 3: Disabling all {len(channels)} channels...")
            try:
                bulk_disable_payload = [
                    {"channel_id": channel['id'], "enabled": False}
                    for channel in channels
                ]
                
                self._patch_api_data(
                    f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
                    token,
                    bulk_disable_payload,
                    settings,
                    logger
                )
                logger.info(f"Successfully disabled all {len(channels)} channels")
                
            except Exception as e:
                logger.error(f"Failed to bulk disable channels: {e}")
                logger.info("Attempting to disable channels individually...")
                
                # Fallback: disable one by one
                for channel in channels:
                    try:
                        self._patch_api_data(
                            f"/api/channels/profiles/{profile_id}/channels/{channel['id']}/",
                            token,
                            {"enabled": False},
                            settings,
                            logger
                        )
                    except Exception as e2:
                        logger.error(f"Failed to disable channel {channel['id']}: {e2}")
            
            # Step 3.5: Group channels and apply visible channel limit
            logger.info("Step 3.5: Grouping channels and applying visibility limit...")
            
            # Group channels by their cleaned name
            channel_groups = {}
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
            
            # Determine which channels to enable
            channels_to_enable = []
            
            for group_key, group_channels in channel_groups.items():
                logger.info(f"Processing channel group: {group_key} ({len(group_channels)} channels)")
                
                # Sort channels in this group by priority
                sorted_channels = self._sort_channels_by_priority(group_channels)
                
                # Filter to only channels with 0-1 streams and not attached
                eligible_channels = [
                    ch for ch in sorted_channels
                    if channel_stream_counts[ch['id']]['stream_count'] <= 1
                    and ch['id'] not in channels_attached_to_others
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
            
            # Trigger frontend refresh
            self._trigger_frontend_refresh(settings, logger)
            
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
                    message_parts.append(f"• {info['name']}: {info.get('reason', 'N/A')}")
                    shown_count += 1
            
            if len(channels_to_enable) > 10:
                message_parts.append(f"... and {len(channels_to_enable) - 10} more enabled channels")
            
            message_parts.append("")
            message_parts.append("Frontend refresh triggered - changes should be visible in the interface shortly.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error managing channel visibility: {str(e)}")
            return {"status": "error", "message": f"Error managing channel visibility: {str(e)}"}

    def clear_csv_exports_action(self, settings, logger):
        """Delete all CSV export files created by this plugin"""
        try:
            export_dir = "/data/exports"
            
            if not os.path.exists(export_dir):
                return {
                    "status": "success",
                    "message": "No export directory found. No files to delete."
                }
            
            # Find all CSV files created by this plugin
            deleted_count = 0
            deleted_files = []
            
            for filename in os.listdir(export_dir):
                if filename.startswith("stream_mapparr_") and filename.endswith(".csv"):
                    filepath = os.path.join(export_dir, filename)
                    try:
                        os.remove(filepath)
                        deleted_count += 1
                        deleted_files.append(filename)
                        logger.info(f"Deleted CSV file: {filename}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filename}: {e}")
            
            if deleted_count == 0:
                return {
                    "status": "success",
                    "message": "No CSV export files found to delete."
                }
            
            message_parts = [
                f"Successfully deleted {deleted_count} CSV export file(s):",
                ""
            ]
            
            # Show first 10 deleted files
            for filename in deleted_files[:10]:
                message_parts.append(f"• {filename}")
            
            if len(deleted_files) > 10:
                message_parts.append(f"• ... and {len(deleted_files) - 10} more files")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error clearing CSV exports: {e}")
            return {"status": "error", "message": f"Error clearing CSV exports: {e}"}


# Export fields and actions for Dispatcharr plugin system
fields = Plugin.fields
actions = Plugin.actions

# Additional exports for Dispatcharr plugin system compatibility
plugin = Plugin()
plugin_instance = Plugin()

# Alternative export names in case Dispatcharr looks for these
stream_mapparr = Plugin()
STREAM_MAPPARR = Plugin()