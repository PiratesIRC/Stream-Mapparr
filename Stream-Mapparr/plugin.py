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
LOGGER = logging.getLogger("plugins.channel_addarr")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

class Plugin:
    """Dispatcharr Stream-Mapparr Plugin"""
    
    name = "Stream-Mapparr"
    version = "0.1"
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
            "help_text": "The name of an existing Channel Profile to process channels from.",
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
    ]
    
    # Quality precedence order
    QUALITY_ORDER = [
        "4K", "[4K]", "(4K)",
        "FHD", "[FHD]", "(FHD)",
        "HD", "[HD]", "(HD)",
        "SD", "[SD]", "(SD)",
        "Slow", "[Slow]", "(Slow)"
    ]
    
    def __init__(self):
        self.processed_data_file = "/data/channel_addarr_processed.json"
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
        for quality in self.QUALITY_ORDER:
            # Match quality with or without brackets/parentheses
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

    def _sort_streams_by_quality(self, streams):
        """Sort streams by quality precedence."""
        def get_quality_index(stream):
            quality = self._extract_quality(stream['name'])
            if quality:
                try:
                    return self.QUALITY_ORDER.index(quality)
                except ValueError:
                    return len(self.QUALITY_ORDER)
            return len(self.QUALITY_ORDER)
        
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

    def _match_streams_to_channel(self, channel, all_streams, logger, ignore_tags=None, known_channels=None, networks_data=None):
        """Find matching streams for a channel based on name similarity."""
        if ignore_tags is None:
            ignore_tags = []
        if known_channels is None:
            known_channels = []
        if networks_data is None:
            networks_data = []
        
        channel_name = channel['name']
        
        # Check if this is an OTA channel
        if self._is_ota_channel(channel_name):
            logger.info(f"Matching OTA channel: {channel_name}")
            matched_streams = self._match_ota_streams(channel_name, all_streams, networks_data, logger)
            
            if matched_streams:
                sorted_streams = self._sort_streams_by_quality(matched_streams)
                logger.info(f"  Sorted {len(sorted_streams)} OTA streams by quality")
                return sorted_streams
            else:
                logger.info(f"  No OTA streams found")
                return []
        
        # Regular channel matching logic for non-OTA channels
        cleaned_channel_name = self._clean_channel_name(channel_name, ignore_tags)
        logger.info(f"Matching streams for channel: {channel_name} (cleaned: {cleaned_channel_name})")
        
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
        
        logger.info(f"  Longer channels to exclude: {longer_channels}")
        
        for stream in all_streams:
            stream_name = stream['name']
            
            # Check if stream matches our channel name (case insensitive)
            pattern = r'(?:^|[:\s])\s*' + escaped_channel_name + r'(?:\s*[\(\|]|$|\s+[\(\|]?$)'
            
            if re.search(pattern, stream_name, re.IGNORECASE):
                # Filter 1: Skip if it's part of a call sign
                call_sign_pattern = r'\b[A-Z]{4,5}\s+' + escaped_channel_name + r'\b'
                if re.search(call_sign_pattern, stream_name):
                    logger.info(f"  Skipped (call sign): {stream_name}")
                    continue
                
                # Filter 2: Skip if stream actually contains a longer channel name
                skip_stream = False
                for longer_channel in longer_channels:
                    escaped_longer = re.escape(longer_channel)
                    longer_pattern = r'(?:^|[:\s])\s*' + escaped_longer + r'(?:\s*[\(\|]|$|\s+[\(\|]?$)'
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
                logger.info(f"  Found match: {stream_name}")
        
        if matching_streams:
            # Sort by quality precedence
            sorted_streams = self._sort_streams_by_quality(matching_streams)
            logger.info(f"  Sorted {len(sorted_streams)} streams by quality")
            return sorted_streams
        
        logger.info(f"  No matching streams found")
        return []

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
            
            if not profile_name:
                return {"status": "error", "message": "Profile Name must be configured in the plugin settings."}
            
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
            
            # Store loaded data including ignore tags
            self.loaded_channels = channels_to_process
            self.loaded_streams = all_streams_data
            
            # Save to file
            processed_data = {
                "loaded_at": datetime.now().isoformat(),
                "profile_name": profile_name,
                "profile_id": profile_id,
                "selected_groups": selected_groups,
                "ignore_tags": ignore_tags,
                "channels": channels_to_process,
                "streams": all_streams_data
            }
            
            with open(self.processed_data_file, 'w') as f:
                json.dump(processed_data, f, indent=2)
            
            logger.info("Channel and stream data loaded and saved successfully")
            
            return {
                "status": "success",
                "message": f"Successfully loaded {len(channels_to_process)} channels from profile '{profile_name}'{group_filter_info}\n\nFound {len(all_streams_data)} streams available for matching.\n\nYou can now run 'Preview Changes' or 'Add Streams to Channels'."
            }
            
        except Exception as e:
            logger.error(f"Error loading channels: {str(e)}")
            return {"status": "error", "message": f"Error loading channels: {str(e)}"}

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

    def _parse_callsign(self, callsign):
        """Extract clean callsign, removing suffixes after dash."""
        if not callsign:
            return None
        
        # Remove anything after dash (e.g., "WLNE-TV" becomes "WLNE")
        if '-' in callsign:
            callsign = callsign.split('-')[0].strip()
        
        return callsign.upper()

    def _is_ota_channel(self, channel_name):
        """Check if a channel name matches OTA pattern."""
        # OTA pattern: "NETWORK - STATE City (CALLSIGN)"
        # Examples: "ABC - TN Chattanooga (WTVC)", "NBC - NY New York (WNBC)"
        ota_pattern = r'^[A-Z]+\s*-\s*[A-Z]{2}\s+.+\([A-Z]+.*\)$'
        return bool(re.match(ota_pattern, channel_name))

    def _extract_ota_info(self, channel_name):
        """Extract network, state, city, and callsign from OTA channel name."""
        # Pattern: "NETWORK - STATE City (CALLSIGN)"
        match = re.match(r'^([A-Z]+)\s*-\s*([A-Z]{2})\s+(.+)\(([A-Z]+[^)]*)\)$', channel_name)
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
                # Additional validation: ensure it's the right network
                # The stream should contain the network name or be validated by context
                if ota_info['network'] in stream_name:
                    matching_streams.append(stream)
                    logger.info(f"  Found OTA stream match: {stream['name']}")
        
        return matching_streams

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
            
            if not channels:
                return {"status": "error", "message": "No channels found in processed data."}
            
            logger.info(f"Previewing changes for {len(channels)} channels with {len(streams)} available streams")
            
            # Match streams to channels
            matches = []
            channels_with_matches = 0
            channels_without_matches = 0
            
            ignore_tags = processed_data.get('ignore_tags', [])

            for channel in channels:
                matched_streams = self._match_streams_to_channel(channel, streams, logger, ignore_tags, known_channels, networks_data)
                
                match_info = {
                    "channel_id": channel['id'],
                    "channel_name": channel['name'],
                    "channel_number": channel.get('channel_number'),
                    "matched_streams": len(matched_streams),
                    "stream_names": [s['name'] for s in matched_streams],
                    "stream_ids": [s['id'] for s in matched_streams]
                }
                
                matches.append(match_info)
                
                if matched_streams:
                    channels_with_matches += 1
                else:
                    channels_without_matches += 1
            
            # Generate CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"channel_addarr_preview_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            
            os.makedirs("/data/exports", exist_ok=True)
            
            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                fieldnames = [
                    'channel_id',
                    'channel_name',
                    'channel_number',
                    'matched_streams_count',
                    'stream_names',
                    'stream_ids'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for match in matches:
                    writer.writerow({
                        'channel_id': match['channel_id'],
                        'channel_name': match['channel_name'],
                        'channel_number': match['channel_number'],
                        'matched_streams_count': match['matched_streams'],
                        'stream_names': ' | '.join(match['stream_names']),
                        'stream_ids': ', '.join(map(str, match['stream_ids']))
                    })
            
            logger.info(f"Preview CSV exported to {filepath}")
            
            # Create summary message
            message_parts = [
                "Preview completed:",
                f"• Total channels: {len(channels)}",
                f"• Channels with matches: {channels_with_matches}",
                f"• Channels without matches: {channels_without_matches}",
                "",
                f"Preview exported to: {filepath}",
                "",
                "Sample matches:"
            ]
            
            # Show first 10 channels
            for match in matches[:10]:
                if match['matched_streams'] > 0:
                    stream_preview = ', '.join(match['stream_names'][:3])
                    if match['matched_streams'] > 3:
                        stream_preview += f" ... (+{match['matched_streams'] - 3} more)"
                    message_parts.append(f"• {match['channel_name']}: {stream_preview}")
                else:
                    message_parts.append(f"• {match['channel_name']}: No matches found")
            
            if len(matches) > 10:
                message_parts.append(f"... and {len(matches) - 10} more channels")
            
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
            
            if not channels:
                return {"status": "error", "message": "No channels found in processed data."}
            
            logger.info(f"Adding streams to {len(channels)} channels")
            
            # Match streams to channels and prepare updates
            matches = []
            channels_updated = 0
            channels_skipped = 0
            
            ignore_tags = processed_data.get('ignore_tags', [])

            for channel in channels:
                matched_streams = self._match_streams_to_channel(channel, streams, logger, ignore_tags, known_channels, networks_data)
                
                match_info = {
                    "channel_id": channel['id'],
                    "channel_name": channel['name'],
                    "channel_number": channel.get('channel_number'),
                    "matched_streams": len(matched_streams),
                    "stream_names": [s['name'] for s in matched_streams],
                    "stream_ids": [s['id'] for s in matched_streams]
                }
                
                matches.append(match_info)
                
                if matched_streams:
                    # Update channels one by one instead of bulk to avoid API issues
                    stream_ids = [s['id'] for s in matched_streams]
                    
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
                        channels_updated += 1
                        
                    except Exception as e:
                        logger.error(f"Failed to update channel {channel['name']} (ID: {channel['id']}): {e}")
                        channels_skipped += 1
                else:
                    channels_skipped += 1
            
            # Trigger M3U refresh
            if channels_updated > 0:
                self._trigger_m3u_refresh(token, settings, logger)
            
            # Generate results CSV
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"channel_addarr_results_{timestamp}.csv"
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
                    'status'
                ]
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for match in matches:
                    writer.writerow({
                        'channel_id': match['channel_id'],
                        'channel_name': match['channel_name'],
                        'channel_number': match['channel_number'],
                        'matched_streams_count': match['matched_streams'],
                        'stream_names': ' | '.join(match['stream_names']),
                        'stream_ids': ', '.join(map(str, match['stream_ids'])),
                        'status': 'Updated' if match['matched_streams'] > 0 else 'No matches'
                    })
            
            logger.info(f"Results CSV exported to {filepath}")
            
            # Create summary message
            message_parts = [
                "Stream addition completed:",
                f"• Total channels processed: {len(channels)}",
                f"• Channels updated: {channels_updated}",
                f"• Channels skipped (no matches or errors): {channels_skipped}",
                "",
                f"Results exported to: {filepath}",
                "",
                "Sample updates:"
            ]
            
            # Show first 10 updated channels
            updated_count = 0
            for match in matches:
                if match['matched_streams'] > 0:
                    stream_preview = ', '.join(match['stream_names'][:3])
                    if match['matched_streams'] > 3:
                        stream_preview += f" ... (+{match['matched_streams'] - 3} more)"
                    message_parts.append(f"• {match['channel_name']}: Added {match['matched_streams']} streams")
                    message_parts.append(f"  Streams: {stream_preview}")
                    updated_count += 1
                    if updated_count >= 10:
                        break
            
            if channels_updated > 10:
                message_parts.append(f"... and {channels_updated - 10} more channels updated")
            
            message_parts.append("")
            message_parts.append("GUI refresh triggered - changes should be visible in the interface shortly.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except Exception as e:
            logger.error(f"Error adding streams to channels: {str(e)}")
            return {"status": "error", "message": f"Error adding streams to channels: {str(e)}"}


# Export fields and actions for Dispatcharr plugin system
fields = Plugin.fields
actions = Plugin.actions

# Additional exports for Dispatcharr plugin system compatibility
plugin = Plugin()
plugin_instance = Plugin()

# Alternative export names in case Dispatcharr looks for these
channel_addarr = Plugin()
CHANNEL_ADDARR = Plugin()