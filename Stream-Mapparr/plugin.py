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
import urllib.request
import urllib.error
import time
import random
import pytz
from datetime import datetime, timedelta
from django.utils import timezone
from django.db import transaction
import threading

# Import FuzzyMatcher from the same directory
from .fuzzy_matcher import FuzzyMatcher
# Import fuzzy_matcher version for CSV header
from . import fuzzy_matcher

# Django model imports - same approach as Event Channel Managarr
from apps.channels.models import Channel, ChannelProfileMembership, ChannelStream, Stream

# Background scheduling globals
_bg_thread = None
_stop_event = threading.Event()

# Setup logging using Dispatcharr's format
LOGGER = logging.getLogger("plugins.stream_mapparr")
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)

# ============================================================================
# CONFIGURATION DEFAULTS - Modify these values to change plugin defaults
# ============================================================================

class PluginConfig:
    """
    Centralized configuration for all plugin default settings.
    Modify these values to change the plugin's default behavior.

    This configuration class contains ALL default values used throughout the plugin,
    making it easy to customize defaults without searching through the entire codebase.

    Categories:
    - Plugin Metadata: Version number
    - Matching Settings: Fuzzy matching behavior
    - Tag Filtering: Which tag types to ignore by default
    - Profile & Group Settings: Default profile/group selections
    - API Settings: Timeouts, rate limiting, caching
    - Scheduling Settings: Timezone, schedule format, CSV export
    - Cache Settings: How long to cache data
    - File Paths: Where to store data files
    - Quality Tag Ordering: Priority order for channels and streams
    """

    # === PLUGIN METADATA ===
    PLUGIN_VERSION = "0.7.3"
    FUZZY_MATCHER_MIN_VERSION = "25.358.0200"  # Requires custom ignore tags Unicode fix

    # === MATCHING SETTINGS ===
    DEFAULT_FUZZY_MATCH_THRESHOLD = 85          # Minimum similarity score (0-100)
    DEFAULT_OVERWRITE_STREAMS = True            # Replace existing streams vs append
    DEFAULT_VISIBLE_CHANNEL_LIMIT = 1           # Channels per group to enable

    # === TAG FILTERING SETTINGS ===
    DEFAULT_IGNORE_QUALITY_TAGS = True          # Ignore [4K], HD, (SD), etc.
    DEFAULT_IGNORE_REGIONAL_TAGS = True         # Ignore East, West, etc.
    DEFAULT_IGNORE_GEOGRAPHIC_TAGS = True       # Ignore US:, UK:, FR:, etc.
    DEFAULT_IGNORE_MISC_TAGS = True             # Ignore (CX), (Backup), etc.
    DEFAULT_IGNORE_TAGS = ""                    # Custom user-defined tags

    # === PROFILE & GROUP SETTINGS ===
    DEFAULT_PROFILE_NAME = ""                   # Required by user
    DEFAULT_SELECTED_GROUPS = ""                # Empty = all groups
    DEFAULT_SELECTED_STREAM_GROUPS = ""         # Empty = all stream groups
    DEFAULT_SELECTED_M3US = ""                  # Empty = all M3U sources

    # === API SETTINGS ===
    DEFAULT_DISPATCHARR_URL = ""                # Required by user
    DEFAULT_RATE_LIMITING = "none"              # Options: none, low, medium, high
    API_REQUEST_TIMEOUT = 30                    # Seconds for API requests
    API_TOKEN_CACHE_DURATION = 30               # Minutes to cache API token

    # === RATE LIMITING DELAYS (seconds) ===
    RATE_LIMIT_NONE = 0.0                       # No rate limiting
    RATE_LIMIT_LOW = 0.1                        # 10 requests/second
    RATE_LIMIT_MEDIUM = 0.5                     # 2 requests/second
    RATE_LIMIT_HIGH = 2.0                       # 1 request/2 seconds
    RATE_LIMIT_MAX_BACKOFF = 60                 # Maximum exponential backoff delay

    # === SCHEDULING SETTINGS ===
    DEFAULT_TIMEZONE = "US/Central"             # Default timezone for scheduled runs
    DEFAULT_SCHEDULED_TIMES = ""                # Empty = no scheduling
    DEFAULT_ENABLE_CSV_EXPORT = True            # Create CSV when streams added

    SCHEDULER_CHECK_INTERVAL = 30               # Seconds between schedule checks
    SCHEDULER_TIME_WINDOW = 30                  # ± seconds to trigger scheduled run
    SCHEDULER_ERROR_WAIT = 60                   # Seconds to wait after error
    SCHEDULER_STOP_TIMEOUT = 5                  # Seconds to wait for graceful shutdown

    # === CACHE SETTINGS ===
    VERSION_CHECK_CACHE_HOURS = 24              # Hours to cache GitHub version check

    # === FILE PATHS ===
    DATA_DIR = "/data"
    EXPORTS_DIR = "/data/exports"
    PROCESSED_DATA_FILE = "/data/stream_mapparr_processed.json"
    VERSION_CHECK_CACHE_FILE = "/data/stream_mapparr_version_check.json"
    SETTINGS_FILE = "/data/stream_mapparr_settings.json"
    OPERATION_LOCK_FILE = "/data/stream_mapparr_operation.lock"

    # === OPERATION LOCK SETTINGS ===
    OPERATION_LOCK_TIMEOUT_MINUTES = 10  # Lock expires after 10 minutes (in case of errors)

    # === PROGRESS TRACKING SETTINGS ===
    ESTIMATED_SECONDS_PER_ITEM = 7.73  # Historical average time per item (from log analysis)

    # === IPTV CHECKER INTEGRATION SETTINGS ===
    DEFAULT_FILTER_DEAD_STREAMS = False  # Filter streams with 0x0 resolution (requires IPTV Checker)
    DEFAULT_WAIT_FOR_IPTV_CHECKER = False  # Wait for IPTV Checker to complete before scheduled runs
    DEFAULT_IPTV_CHECKER_MAX_WAIT_HOURS = 6  # Maximum hours to wait for IPTV Checker
    IPTV_CHECKER_PROGRESS_FILE = "/data/iptv_checker_progress.json"  # IPTV Checker progress file
    IPTV_CHECKER_CHECK_INTERVAL = 60  # Check IPTV Checker status every 60 seconds

    # === QUALITY TAG ORDERING ===
    # Order for prioritizing channels (higher quality first)
    CHANNEL_QUALITY_TAG_ORDER = ["[4K]", "[UHD]", "[FHD]", "[HD]", "[SD]", "[Unknown]", "[Slow]", ""]

    # Order for sorting streams (higher quality first)
    STREAM_QUALITY_ORDER = [
        "[4K]", "(4K)", "4K",
        "[UHD]", "(UHD)", "UHD",
        "[FHD]", "(FHD)", "FHD",
        "[HD]", "(HD)", "HD", "(H)",
        "[SD]", "(SD)", "SD",
        "(F)", "(D)",
        "Slow", "[Slow]", "(Slow)"
    ]

# ============================================================================
# END CONFIGURATION
# ============================================================================

class ProgressTracker:
    """
    Tracks operation progress with ETA calculation and periodic updates.
    Provides minute-by-minute progress reporting with estimated time remaining.
    """
    def __init__(self, total_items, action_id, logger, context=None, send_progress_callback=None):
        """
        Initialize progress tracker.
        
        Args:
            total_items: Total number of items to process
            action_id: Action identifier for logging
            logger: Logger instance
            context: Optional context for WebSocket updates
            send_progress_callback: Callback function for sending progress updates
        """
        self.total_items = total_items
        self.action_id = action_id
        self.logger = logger
        self.context = context
        self.send_progress_callback = send_progress_callback
        
        # Time tracking
        self.start_time = time.time()
        self.last_update_time = self.start_time
        self.update_interval = 60  # Update every 60 seconds
        
        # Progress tracking
        self.processed_items = 0
        self.last_reported_progress = 0
        
        # Base progress range (0-100)
        self.base_progress_start = 0
        self.base_progress_end = 100
        
        # Calculate initial ETA based on historical average
        initial_eta_seconds = total_items * PluginConfig.ESTIMATED_SECONDS_PER_ITEM
        initial_eta_str = self._format_eta(initial_eta_seconds)
        
        self.logger.info(f"[Stream-Mapparr] {action_id}: Starting to process {total_items} items (estimated ~{initial_eta_str})")
    
    def set_progress_range(self, start, end):
        """
        Set the progress range for this tracker within the overall operation.
        For example, if this tracker handles 30-80% of the overall progress.
        """
        self.base_progress_start = start
        self.base_progress_end = end
    
    def update(self, items_processed=1):
        """
        Update progress by incrementing processed items.
        Automatically sends progress updates every minute.
        
        Args:
            items_processed: Number of items just processed (default: 1)
        """
        self.processed_items += items_processed
        current_time = time.time()
        
        # Check if we should send an update (every minute)
        time_since_update = current_time - self.last_update_time
        
        # Always send update if it's been more than update_interval seconds
        should_update = time_since_update >= self.update_interval
        
        # Also send update if we're at 100%
        is_complete = self.processed_items >= self.total_items
        
        if should_update or is_complete:
            self._send_update()
            self.last_update_time = current_time
    
    def _send_update(self):
        """Send a progress update with ETA calculation"""
        # Calculate progress percentage within our range
        if self.total_items > 0:
            progress_ratio = min(1.0, self.processed_items / self.total_items)
        else:
            progress_ratio = 1.0
        
        # Map to our progress range
        progress = self.base_progress_start + (progress_ratio * (self.base_progress_end - self.base_progress_start))
        progress = int(progress)
        
        # Calculate ETA
        elapsed_time = time.time() - self.start_time
        
        if self.processed_items > 0 and self.processed_items < self.total_items:
            # Calculate time per item
            time_per_item = elapsed_time / self.processed_items
            items_remaining = self.total_items - self.processed_items
            eta_seconds = time_per_item * items_remaining
            
            # Format ETA
            eta_str = self._format_eta(eta_seconds)
            message = f"Processed {self.processed_items}/{self.total_items} items. ETA: {eta_str}"
        elif self.processed_items >= self.total_items:
            # Complete
            elapsed_str = self._format_time(elapsed_time)
            message = f"Completed {self.total_items} items in {elapsed_str}"
        else:
            # Just started
            message = f"Processing {self.total_items} items..."
        
        # Log the update
        self.logger.info(f"[Stream-Mapparr] {self.action_id}: {message}")
        
        # Send WebSocket update if callback provided
        if self.send_progress_callback and progress != self.last_reported_progress:
            self.send_progress_callback(self.action_id, 'running', progress, message, self.context)
            self.last_reported_progress = progress
    
    def _format_eta(self, seconds):
        """Format ETA in human-readable format"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            remaining_seconds = int(seconds % 60)
            return f"{minutes}m {remaining_seconds}s"
        else:
            hours = int(seconds / 3600)
            remaining_minutes = int((seconds % 3600) / 60)
            return f"{hours}h {remaining_minutes}m"
    
    def _format_time(self, seconds):
        """Format elapsed time in human-readable format"""
        return self._format_eta(seconds)
    
    def force_update(self):
        """Force an immediate progress update regardless of time interval"""
        self._send_update()
        self.last_update_time = time.time()


class SmartRateLimiter:
    """
    Handles rate limiting with exponential backoff for 429/5xx errors.
    Uses standard library only.
    """
    def __init__(self, setting_value="medium", logger=None):
        self.logger = logger
        self.disabled = setting_value == "none"

        # Define delays (seconds) based on settings - uses PluginConfig values
        if self.disabled:
            self.base_delay = PluginConfig.RATE_LIMIT_NONE
        elif setting_value == "high":
            self.base_delay = PluginConfig.RATE_LIMIT_HIGH
        elif setting_value == "low":
            self.base_delay = PluginConfig.RATE_LIMIT_LOW
        else:
            self.base_delay = PluginConfig.RATE_LIMIT_MEDIUM

        self.current_delay = self.base_delay
        self.consecutive_errors = 0

    def wait(self):
        """Call this before making a request"""
        if not self.disabled and self.current_delay > 0:
            time.sleep(self.current_delay)

    def report_success(self):
        """Call this after a successful 200 OK request"""
        self.consecutive_errors = 0
        if self.current_delay > self.base_delay:
            self.current_delay = max(self.base_delay, self.current_delay / 2)

    def report_error(self, status_code):
        """Call this when an API request fails"""
        self.consecutive_errors += 1

        if status_code == 429 or status_code >= 500:
            backoff = min(PluginConfig.RATE_LIMIT_MAX_BACKOFF, self.base_delay * (2 ** self.consecutive_errors))
            jitter = backoff * 0.1 * random.random() # +/- 10% jitter
            self.current_delay = backoff + jitter

            if self.logger:
                self.logger.warning(f"[Stream-Mapparr] Rate limit/Server error ({status_code}). Backing off to {self.current_delay:.2f}s")
        else:
            self.current_delay = self.base_delay

class Plugin:
    """Dispatcharr Stream-Mapparr Plugin"""

    name = "Stream-Mapparr"
    version = PluginConfig.PLUGIN_VERSION
    description = "🎯 Automatically add matching streams to channels based on name similarity and quality precedence with enhanced fuzzy matching"

    @property
    def fields(self):
        """Dynamically generate settings fields including channel database selection."""
        version_info = {'message': f"Current version: {self.version}", 'status': 'unknown'}
        try:
            version_info = self._check_version_update()
        except Exception as e:
            LOGGER.debug(f"[Stream-Mapparr] Error checking version update: {e}")

        static_fields = [
            {
                "id": "version_status",
                "type": "info",
                "label": version_info['message'],
            },
            {
                "id": "overwrite_streams",
                "label": "🔄 Overwrite Existing Streams",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_OVERWRITE_STREAMS,
                "help_text": "If enabled, all existing streams will be removed and replaced with matched streams. If disabled, only new streams will be added (existing streams preserved).",
            },
            {
                "id": "fuzzy_match_threshold",
                "label": "🎯 Fuzzy Match Threshold",
                "type": "number",
                "default": PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD,
                "help_text": f"Minimum similarity score (0-100) for fuzzy matching. Higher values require closer matches. Default: {PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD}",
            },
            {
                "id": "dispatcharr_url",
                "label": "🌐 Dispatcharr URL",
                "type": "string",
                "default": PluginConfig.DEFAULT_DISPATCHARR_URL,
                "placeholder": "http://192.168.1.10:9191",
                "help_text": "URL of your Dispatcharr instance (from your browser address bar). Example: http://127.0.0.1:9191",
            },
            {
                "id": "dispatcharr_username",
                "label": "👤 Dispatcharr Admin Username",
                "type": "string",
                "help_text": "Your admin username for the Dispatcharr UI. Required for API access.",
            },
            {
                "id": "dispatcharr_password",
                "label": "🔑 Dispatcharr Admin Password",
                "type": "string",
                "input_type": "password",
                "help_text": "Your admin password for the Dispatcharr UI. Required for API access.",
            },
            {
                "id": "profile_name",
                "label": "📋 Profile Name",
                "type": "string",
                "default": PluginConfig.DEFAULT_PROFILE_NAME,
                "placeholder": "Sports, Movies, News",
                "help_text": "*** Required Field *** - The name(s) of existing Channel Profile(s) to process channels from. Multiple profiles can be specified separated by commas.",
            },
            {
                "id": "selected_groups",
                "label": "📁 Channel Groups (comma-separated)",
                "type": "string",
                "default": PluginConfig.DEFAULT_SELECTED_GROUPS,
                "placeholder": "Sports, News, Entertainment",
                "help_text": "Specific channel groups to process, or leave empty for all groups.",
            },
            {
                "id": "selected_stream_groups",
                "label": "📺 Stream Groups (comma-separated)",
                "type": "string",
                "default": PluginConfig.DEFAULT_SELECTED_STREAM_GROUPS,
                "placeholder": "TVE, Cable, Satellite",
                "help_text": "Specific stream groups to use when matching, or leave empty for all stream groups. Multiple groups can be specified separated by commas.",
            },
            {
                "id": "selected_m3us",
                "label": "📡 M3U Sources (comma-separated, prioritized)",
                "type": "string",
                "default": PluginConfig.DEFAULT_SELECTED_M3US,
                "placeholder": "IPTV Provider 1, Local M3U, Sports",
                "help_text": "Specific M3U sources to use when matching, or leave empty for all M3U sources. Multiple M3U sources can be specified separated by commas. Order matters: streams from earlier M3U sources are prioritized over later ones when sorting by quality.",
            },
            {
                "id": "ignore_tags",
                "label": "🏷️ Ignore Tags (comma-separated)",
                "type": "string",
                "default": PluginConfig.DEFAULT_IGNORE_TAGS,
                "placeholder": "4K, [4K], \" East\", \"[Dead]\"",
                "help_text": "Tags to ignore when matching streams. Use quotes to preserve spaces/special chars (e.g., \" East\" for tags with leading space).",
            },
            {
                "id": "ignore_quality_tags",
                "label": "🎬 Ignore Quality Tags",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_IGNORE_QUALITY_TAGS,
                "help_text": "If enabled, all quality indicators will be ignored in any format and position (e.g., 4K, [4K], (4K), FHD, [FHD], (FHD), HD, SD at beginning, middle, or end of name).",
            },
            {
                "id": "ignore_regional_tags",
                "label": "🌍 Ignore Regional Tags",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_IGNORE_REGIONAL_TAGS,
                "help_text": "If enabled, hardcoded regional tags like 'East' will be ignored during matching.",
            },
            {
                "id": "ignore_geographic_tags",
                "label": "🗺️ Ignore Geographic Tags",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_IGNORE_GEOGRAPHIC_TAGS,
                "help_text": "If enabled, all country codes will be ignored during matching (e.g., US, USA, US:, |FR|, FR -, [UK], etc.).",
            },
            {
                "id": "ignore_misc_tags",
                "label": "🏷️ Ignore Miscellaneous Tags",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_IGNORE_MISC_TAGS,
                "help_text": "If enabled, all content within parentheses will be ignored during matching (e.g., (CX), (B), (PRIME), (Backup)).",
            },
            {
                "id": "visible_channel_limit",
                "label": "👁️ Visible Channel Limit",
                "type": "number",
                "default": PluginConfig.DEFAULT_VISIBLE_CHANNEL_LIMIT,
                "help_text": "Number of channels that will be visible and have streams added. Channels are prioritized by quality tags, then by channel number.",
            },
            {
                "id": "rate_limiting",
                "label": "⏳ Rate Limiting",
                "type": "select",
                "options": [
                    {"label": "None (Disabled)", "value": "none"},
                    {"label": "Low (Fast)", "value": "low"},
                    {"label": "Medium (Standard)", "value": "medium"},
                    {"label": "High (Slow)", "value": "high"},
                ],
                "default": PluginConfig.DEFAULT_RATE_LIMITING,
                "help_text": "Controls delay between API calls. None=No delays, Low=Fast/Aggressive, Medium=Standard, High=Slow/Safe.",
            },
            {
                "id": "timezone",
                "label": "🌍 Timezone",
                "type": "select",
                "default": PluginConfig.DEFAULT_TIMEZONE,
                "help_text": "Timezone for scheduled runs. Schedule times below will be converted to UTC.",
                "options": [
                    {"label": "UTC (Coordinated Universal Time)", "value": "UTC"},
                    {"label": "US/Eastern (EST/EDT) - New York", "value": "US/Eastern"},
                    {"label": "US/Central (CST/CDT) - Chicago", "value": "US/Central"},
                    {"label": "US/Mountain (MST/MDT) - Denver", "value": "US/Mountain"},
                    {"label": "US/Pacific (PST/PDT) - Los Angeles", "value": "US/Pacific"},
                    {"label": "America/Phoenix (MST - no DST)", "value": "America/Phoenix"},
                    {"label": "Europe/London (GMT/BST)", "value": "Europe/London"},
                    {"label": "Europe/Paris (CET/CEST)", "value": "Europe/Paris"},
                    {"label": "Europe/Berlin (CET/CEST)", "value": "Europe/Berlin"},
                    {"label": "Asia/Dubai (GST)", "value": "Asia/Dubai"},
                    {"label": "Asia/Tokyo (JST)", "value": "Asia/Tokyo"},
                    {"label": "Asia/Shanghai (CST)", "value": "Asia/Shanghai"},
                    {"label": "Australia/Sydney (AEDT/AEST)", "value": "Australia/Sydney"}
                ]
            },
            {
                "id": "filter_dead_streams",
                "label": "🚫 Filter Dead Streams",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_FILTER_DEAD_STREAMS,
                "help_text": "Skip streams with 0x0 resolution (dead streams detected by IPTV Checker). Requires IPTV Checker to have run first and updated stream metadata.",
            },
            {
                "id": "wait_for_iptv_checker",
                "label": "⏳ Wait for IPTV Checker Completion",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_WAIT_FOR_IPTV_CHECKER,
                "help_text": "When running on schedule, wait for IPTV Checker to complete before starting stream assignment. Ensures dead streams are filtered out. Only applies to scheduled runs, not manual runs.",
            },
            {
                "id": "iptv_checker_max_wait_hours",
                "label": "⏲️ IPTV Checker Max Wait (hours)",
                "type": "number",
                "default": PluginConfig.DEFAULT_IPTV_CHECKER_MAX_WAIT_HOURS,
                "help_text": "Maximum hours to wait for IPTV Checker to complete. If IPTV Checker is still running after this time, Stream-Mapparr will proceed anyway. Default: 6 hours.",
            },
            {
                "id": "dry_run_mode",
                "label": "🧪 Dry Run Mode",
                "type": "boolean",
                "default": False,
                "help_text": "When enabled, preview actions without making database changes. Exports CSV report only. When disabled, actually perform stream matching, sorting, and assignment. Applies to both manual actions and scheduled runs.",
            },
            {
                "id": "scheduled_times",
                "label": "⏰ Scheduled Run Times (24-hour format)",
                "type": "string",
                "default": PluginConfig.DEFAULT_SCHEDULED_TIMES,
                "placeholder": "0600,1300,1800",
                "help_text": "Comma-separated times to run automatically each day (24-hour format). Example: 0600,1300,1800 runs at 6 AM, 1 PM, and 6 PM daily. Leave blank to disable scheduling.",
            },
            {
                "id": "scheduled_sort_streams",
                "label": "🔄 Schedule: Sort Streams",
                "type": "boolean",
                "default": False,
                "help_text": "When enabled, scheduled runs will sort existing alternate streams by quality (4K → UHD → FHD → HD → SD). Only affects channels that already have multiple streams assigned.",
            },
            {
                "id": "scheduled_match_streams",
                "label": "✅ Schedule: Match & Assign Streams",
                "type": "boolean",
                "default": True,
                "help_text": "When enabled, scheduled runs will match and assign new streams to channels using fuzzy matching. This is the main stream assignment feature.",
            },
            {
                "id": "enable_scheduled_csv_export",
                "label": "📄 Enable CSV Export",
                "type": "boolean",
                "default": PluginConfig.DEFAULT_ENABLE_CSV_EXPORT,
                "help_text": "If enabled, a CSV file will be created when streams are matched, sorted, or assigned (both manual and scheduled runs). Always creates CSV in dry run mode regardless of this setting.",
            },
        ]

        try:
            databases = self._get_channel_databases()

            if databases:
                for db_info in databases:
                    db_id = db_info['id']
                    db_label = db_info['label']
                    db_default = db_info['default']

                    static_fields.append({
                        "id": f"db_enabled_{db_id}",
                        "type": "boolean",
                        "label": f"Enable {db_label}",
                        "help_text": f"Enable or disable the {db_label} channel database for matching.",
                        "default": db_default
                    })
            else:
                static_fields.append({
                    "id": "no_databases_found",
                    "type": "info",
                    "label": "⚠️ No channel databases found. Place XX_channels.json files in the plugin directory.",
                })
        except Exception as e:
            LOGGER.error(f"[Stream-Mapparr] Error loading channel databases for settings: {e}")
            static_fields.append({
                "id": "database_error",
                "type": "info",
                "label": f"⚠️ Error loading channel databases: {e}",
            })

        return static_fields

    actions = [
        {
            "id": "validate_settings",
            "label": "✅ Validate Settings",
            "description": "Validate all plugin settings (profiles, groups, API connection, etc.)",
        },
        {
            "id": "update_schedule",
            "label": "💾 Update Schedule",
            "description": "Save settings and update the scheduled run times. Use this after changing any settings.",
        },
        {
            "id": "cleanup_periodic_tasks",
            "label": "🧹 Cleanup Orphaned Tasks",
            "description": "Remove any orphaned Celery periodic tasks from old plugin versions",
            "confirm": { "required": True, "title": "Cleanup Orphaned Tasks?", "message": "This will remove any old Celery Beat tasks created by previous versions of this plugin. Continue?" }
        },
        {
            "id": "add_streams_to_channels",
            "label": "✅ Match & Assign Streams",
            "description": "Match and assign streams to channels using fuzzy matching. Respects 'Dry Run Mode' setting - preview only if enabled, actually assigns if disabled. May take several minutes - monitor Docker logs (docker logs -f dispatcharr) for progress and completion.",
            "confirm": {
                "required": True,
                "title": "Match & Assign Streams?",
                "message": "This will match and assign (or preview if dry run enabled) streams to channels. Continue?"
            }
        },
        {
            "id": "sort_streams",
            "label": "🔄 Sort Alternate Streams",
            "description": "Sort existing alternate streams by quality (4K → UHD → FHD → HD → SD). Only affects channels with multiple streams already assigned. Respects 'Dry Run Mode' setting. Monitor Docker logs (docker logs -f dispatcharr) for progress.",
            "confirm": {
                "required": True,
                "title": "Sort Alternate Streams?",
                "message": "This will sort (or preview if dry run enabled) existing alternate streams by quality for all channels. Continue?"
            }
        },
        {
            "id": "match_us_ota_only",
            "label": "📡 Match US OTA Only",
            "description": "Match ONLY US Over-The-Air broadcast channels by callsign. Uses US_channels.json as authoritative source. Searches streams for uppercase callsigns only (e.g., WKRG, WABC). Respects 'Dry Run Mode' setting. Monitor Docker logs (docker logs -f dispatcharr) for progress.",
            "confirm": {
                "required": True,
                "title": "Match US OTA Only?",
                "message": "This will match and assign (or preview if dry run enabled) streams to US OTA channels using callsign matching only. Channels without valid US callsigns will be skipped. Continue?"
            }
        },
        {
            "id": "manage_channel_visibility",
            "label": "👁️ Manage Channel Visibility",
            "description": "Disable all channels, then enable only channels with 1 or more streams. Monitor Docker logs (docker logs -f dispatcharr) for progress.",
            "confirm": {
                "required": True,
                "title": "Manage Channel Visibility?",
                "message": "This will disable ALL channels in the profile, then enable only channels with 1 or more streams that are not attached to other channels. Continue?"
            }
        },
        {
            "id": "clear_csv_exports",
            "label": "🗑️ Clear CSV Exports",
            "description": "Delete all CSV export files created by this plugin",
            "confirm": {
                "required": True,
                "title": "Clear CSV Exports?",
                "message": "This will delete all CSV export files created by this plugin. Continue?"
            }
        },
        {
            "id": "clear_operation_lock",
            "label": "🔓 Clear Operation Lock",
            "description": "Manually clear the operation lock file if it's stuck (e.g., after a container restart). Only use if no operation is actually running.",
            "confirm": {
                "required": True,
                "title": "Clear Operation Lock?",
                "message": "This will clear the operation lock. Only do this if you're certain no operation is currently running. Continue?"
            }
        },
    ]

    # Use config values for quality tag ordering
    CHANNEL_QUALITY_TAG_ORDER = PluginConfig.CHANNEL_QUALITY_TAG_ORDER
    STREAM_QUALITY_ORDER = PluginConfig.STREAM_QUALITY_ORDER

    def __init__(self):
        # -- SINGLETON GUARD --
        # Ensure init logic runs only once even if Dispatcharr instantiates multiple times
        if getattr(self, '_initialized', False):
            return
        self._initialized = True

        # Use config values for file paths
        self.processed_data_file = PluginConfig.PROCESSED_DATA_FILE
        self.version_check_cache_file = PluginConfig.VERSION_CHECK_CACHE_FILE
        self.settings_file = PluginConfig.SETTINGS_FILE
        self.loaded_channels = []
        self.loaded_streams = []
        self.channel_stream_matches = []
        self.fuzzy_matcher = None
        self.api_token = None
        self.token_expiration = None
        self.saved_settings = {}

        LOGGER.info(f"[Stream-Mapparr] {self.name} Plugin v{self.version} initialized")

    def on_load(self, context):
        """Called when plugin is loaded"""
        LOGGER.info(f"[Stream-Mapparr] Loading {self.name} v{self.version}")
        self._load_settings()

    def on_unload(self):
        """Called when plugin is unloaded - cleanup schedules"""
        LOGGER.info(f"[Stream-Mapparr] Unloading {self.name}")
        self._stop_background_scheduler()

    def _load_settings(self):
        """Load saved settings from disk"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    self.saved_settings = json.load(f)
                    LOGGER.debug("[Stream-Mapparr] Loaded saved settings")
                    # Start background scheduler with loaded settings
                    self._start_background_scheduler(self.saved_settings)
            else:
                self.saved_settings = {}
        except Exception as e:
            LOGGER.error(f"[Stream-Mapparr] Error loading settings: {e}")
            self.saved_settings = {}

    def _save_settings(self, settings):
        """Save settings to disk"""
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=2)
            self.saved_settings = settings
            LOGGER.info("[Stream-Mapparr] Settings saved successfully")
        except Exception as e:
            LOGGER.error(f"[Stream-Mapparr] Error saving settings: {e}")

    def update_schedule_action(self, settings, logger):
        """Save settings and update scheduled tasks"""
        try:
            scheduled_times_str = settings.get("scheduled_times") or ""
            scheduled_times_str = scheduled_times_str.strip() if scheduled_times_str else ""
            logger.debug(f"[Stream-Mapparr] Update Schedule - scheduled_times value: '{scheduled_times_str}'")

            # Save settings to disk
            self._save_settings(settings)
            
            # Start/restart the background scheduler
            self._start_background_scheduler(settings)
            
            if scheduled_times_str:
                times = self._parse_scheduled_times(scheduled_times_str)
                if times:
                    tz_str = self._get_system_timezone(settings)
                    time_list = [t.strftime('%H:%M') for t in times]
                    return {
                        "status": "success",
                        "message": f"Schedule updated successfully!\n\nScheduled to run daily at: {', '.join(time_list)} ({tz_str})\n\nBackground scheduler is running."
                    }
                else:
                    return {
                        "status": "error",
                        "message": "Invalid time format. Please use HHMM format (e.g., 0600,1300,1800)"
                    }
            else:
                self._stop_background_scheduler()
                return {
                    "status": "success",
                    "message": "Scheduled times cleared. Background scheduler stopped."
                }
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error updating schedule: {e}")
            return {"status": "error", "message": f"Error updating schedule: {e}"}

    def cleanup_periodic_tasks_action(self, settings, logger):
        """Remove orphaned Celery periodic tasks from old plugin versions"""
        try:
            from django_celery_beat.models import PeriodicTask
            
            # Find all periodic tasks created by this plugin
            tasks = PeriodicTask.objects.filter(name__startswith='stream_mapparr_')
            task_count = tasks.count()
            
            if task_count == 0:
                return {
                    "status": "success",
                    "message": "No orphaned periodic tasks found. Database is clean!"
                }
            
            # Get task names before deletion
            task_names = list(tasks.values_list('name', flat=True))
            
            # Delete the tasks
            deleted = tasks.delete()
            
            logger.info(f"Deleted {deleted[0]} orphaned periodic tasks")
            
            message_parts = [
                f"Successfully removed {task_count} orphaned Celery periodic task(s):",
                ""
            ]
            
            # Show deleted task names
            for task_name in task_names[:10]:
                message_parts.append(f"• {task_name}")
            
            if len(task_names) > 10:
                message_parts.append(f"• ... and {len(task_names) - 10} more tasks")
            
            message_parts.append("")
            message_parts.append("These were leftover from older plugin versions that used Celery scheduling.")
            message_parts.append("The plugin now uses background threading instead.")
            
            return {
                "status": "success",
                "message": "\n".join(message_parts)
            }
            
        except ImportError:
            return {
                "status": "error",
                "message": "django_celery_beat not available. No cleanup needed."
            }
        except Exception as e:
            logger.error(f"Error cleaning up periodic tasks: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error cleaning up periodic tasks: {e}"}

    def _get_system_timezone(self, settings):
        """Get the system timezone from settings"""
        # First check if user specified a timezone in plugin settings
        if settings.get('timezone'):
            user_tz = settings.get('timezone')
            LOGGER.info(f"Using user-specified timezone: {user_tz}")
            return user_tz
        
        # Otherwise use configured default timezone
        LOGGER.info(f"Using default timezone: {PluginConfig.DEFAULT_TIMEZONE}")
        return PluginConfig.DEFAULT_TIMEZONE
        
    def _parse_scheduled_times(self, scheduled_times_str):
        """Parse scheduled times string into list of datetime.time objects"""
        if not scheduled_times_str or not scheduled_times_str.strip():
            return []
        
        times = []
        for time_str in scheduled_times_str.split(','):
            time_str = time_str.strip()
            if len(time_str) == 4 and time_str.isdigit():
                hour = int(time_str[:2])
                minute = int(time_str[2:])
                if 0 <= hour < 24 and 0 <= minute < 60:
                    times.append(datetime.strptime(time_str, '%H%M').time())
        return times

    def _start_background_scheduler(self, settings):
        """Start background scheduler thread"""
        global _bg_thread
        
        # Stop existing scheduler if running
        self._stop_background_scheduler()
        
        # Parse scheduled times
        scheduled_times_str = settings.get("scheduled_times") or ""
        scheduled_times_str = scheduled_times_str.strip() if scheduled_times_str else ""
        if not scheduled_times_str:
            LOGGER.info("[Stream-Mapparr] No scheduled times configured, scheduler not started")
            return
        
        scheduled_times = self._parse_scheduled_times(scheduled_times_str)
        if not scheduled_times:
            LOGGER.info("[Stream-Mapparr] No valid scheduled times, scheduler not started")
            return
        
        # Start new scheduler thread
        def scheduler_loop():
            import pytz

            # Get timezone from settings
            tz_str = self._get_system_timezone(settings)
            try:
                local_tz = pytz.timezone(tz_str)
            except pytz.exceptions.UnknownTimeZoneError:
                LOGGER.error(f"[Stream-Mapparr] Unknown timezone: {tz_str}, falling back to {PluginConfig.DEFAULT_TIMEZONE}")
                local_tz = pytz.timezone(PluginConfig.DEFAULT_TIMEZONE)

            # Initialize last run tracker to prevent immediate execution
            # when scheduler starts at a time that matches a scheduled time
            last_run = {}

            LOGGER.info(f"[Stream-Mapparr] Scheduler timezone: {tz_str}")
            LOGGER.info(f"[Stream-Mapparr] Scheduler initialized - will run at next scheduled time (not immediately)")
            
            while not _stop_event.is_set():
                try:
                    now = datetime.now(local_tz)
                    current_date = now.date()
                    
                    # Check each scheduled time
                    for scheduled_time in scheduled_times:
                        # Create a datetime for the scheduled time today in the local timezone
                        scheduled_dt = local_tz.localize(datetime.combine(current_date, scheduled_time))
                        time_diff = (scheduled_dt - now).total_seconds()
                        
                        # Run if within configured time window and have not run today for this time
                        if -PluginConfig.SCHEDULER_TIME_WINDOW <= time_diff <= PluginConfig.SCHEDULER_TIME_WINDOW and last_run.get(scheduled_time) != current_date:
                            LOGGER.info(f"[Stream-Mapparr] Scheduled scan triggered at {now.strftime('%Y-%m-%d %H:%M %Z')}")
                            try:
                                # Step 0: Wait for IPTV Checker if enabled
                                wait_result = self._wait_for_iptv_checker_completion(settings, LOGGER)
                                if not wait_result:
                                    LOGGER.warning("[Stream-Mapparr] IPTV Checker wait timed out, proceeding anyway")
                                
                                # Step 1: Load/Process Channels
                                LOGGER.info("[Stream-Mapparr] Step 1/2: Loading and processing channels...")
                                load_result = self.load_process_channels_action(settings, LOGGER)
                                
                                if load_result.get("status") == "success":
                                    LOGGER.info(f"[Stream-Mapparr] {load_result.get('message', 'Channels loaded successfully')}")
                                    
                                    # Get scheduled task settings
                                    do_sort = settings.get('scheduled_sort_streams', False)
                                    if isinstance(do_sort, str):
                                        do_sort = do_sort.lower() in ('true', 'yes', '1')
                                    
                                    do_match = settings.get('scheduled_match_streams', True)
                                    if isinstance(do_match, str):
                                        do_match = do_match.lower() in ('true', 'yes', '1')
                                    
                                    step = 2
                                    total_steps = (2 if do_sort else 0) + (1 if do_match else 0) + 1
                                    
                                    # Step 2: Sort Streams (if enabled)
                                    if do_sort:
                                        LOGGER.info(f"[Stream-Mapparr] Step {step}/{total_steps}: Sorting alternate streams...")
                                        sort_result = self.sort_streams_action(settings, LOGGER)
                                        
                                        if sort_result.get("status") == "success":
                                            LOGGER.info(f"[Stream-Mapparr] {sort_result.get('message', 'Streams sorted successfully')}")
                                        else:
                                            LOGGER.error(f"[Stream-Mapparr] Failed to sort streams: {sort_result.get('message', 'Unknown error')}")
                                        
                                        step += 1
                                    
                                    # Step 3: Match & Assign Streams (if enabled)
                                    if do_match:
                                        LOGGER.info(f"[Stream-Mapparr] Step {step}/{total_steps}: Matching and assigning streams...")
                                        add_result = self.add_streams_to_channels_action(settings, LOGGER, is_scheduled=True)
                                        
                                        if add_result.get("status") == "success":
                                            LOGGER.info(f"[Stream-Mapparr] {add_result.get('message', 'Streams added successfully')}")
                                        else:
                                            LOGGER.error(f"[Stream-Mapparr] Failed to add streams: {add_result.get('message', 'Unknown error')}")
                                    
                                    LOGGER.info("[Stream-Mapparr] Scheduled run completed successfully")
                                else:
                                    LOGGER.error(f"[Stream-Mapparr] Failed to load channels: {load_result.get('message', 'Unknown error')}")
                                    LOGGER.error("[Stream-Mapparr] Scheduled run aborted - cannot proceed without channel data")
                                    
                            except Exception as e:
                                LOGGER.error(f"[Stream-Mapparr] Error in scheduled scan: {e}")
                                import traceback
                                LOGGER.error(f"[Stream-Mapparr] Traceback: {traceback.format_exc()}")
                            
                            # Mark as executed for today's date
                            last_run[scheduled_time] = current_date
                            break
                    
                    # Sleep for configured interval
                    _stop_event.wait(PluginConfig.SCHEDULER_CHECK_INTERVAL)

                except Exception as e:
                    LOGGER.error(f"[Stream-Mapparr] Error in scheduler loop: {e}")
                    _stop_event.wait(PluginConfig.SCHEDULER_ERROR_WAIT)
        
        _bg_thread = threading.Thread(target=scheduler_loop, name="stream-mapparr-scheduler", daemon=True)
        _bg_thread.start()
        LOGGER.info(f"[Stream-Mapparr] Background scheduler started for times: {[t.strftime('%H:%M') for t in scheduled_times]}")

    def _stop_background_scheduler(self):
        """Stop background scheduler thread"""
        global _bg_thread
        if _bg_thread and _bg_thread.is_alive():
            LOGGER.info("Stopping background scheduler")
            _stop_event.set()
            _bg_thread.join(timeout=PluginConfig.SCHEDULER_STOP_TIMEOUT)
            _stop_event.clear()
            LOGGER.info("Background scheduler stopped")

    def _check_version_update(self):
        """Check if a new version is available on GitHub."""
        current_version = self.version
        github_owner = "PiratesIRC"
        github_repo = "Stream-Mapparr"
        result = {
            'message': f"Current version: {current_version}",
            'status': 'unknown'
        }
        try:
            cache_data = {}
            should_check = True
            if os.path.exists(self.version_check_cache_file):
                try:
                    with open(self.version_check_cache_file, 'r', encoding='utf-8') as f:
                        cache_data = json.load(f)
                    cached_plugin_version = cache_data.get('plugin_version')
                    last_check_str = cache_data.get('last_check')
                    if cached_plugin_version == current_version and last_check_str:
                        last_check = datetime.fromisoformat(last_check_str)
                        time_diff = datetime.now() - last_check
                        if time_diff < timedelta(hours=PluginConfig.VERSION_CHECK_CACHE_HOURS):
                            should_check = False
                            latest_version = cache_data.get('latest_version')
                            if latest_version and latest_version != current_version:
                                result = {'message': f"🎉 Update available! Current: {current_version} → Latest: {latest_version}", 'status': 'update_available'}
                            else:
                                result = {'message': f"✅ You are up to date (v{current_version})", 'status': 'up_to_date'}
                except Exception as e:
                    LOGGER.debug(f"[Stream-Mapparr] Error reading version cache: {e}")
                    should_check = True
            if should_check:
                latest_version = self._get_latest_version(github_owner, github_repo)
                cache_data = {'plugin_version': current_version, 'latest_version': latest_version, 'last_check': datetime.now().isoformat()}
                try:
                    with open(self.version_check_cache_file, 'w', encoding='utf-8') as f:
                        json.dump(cache_data, f, indent=2)
                except Exception as e:
                    LOGGER.debug(f"[Stream-Mapparr] Error writing version cache: {e}")
                if latest_version and latest_version != current_version:
                    result = {'message': f"🎉 Update available! Current: {current_version} → Latest: {latest_version}", 'status': 'update_available'}
                elif latest_version:
                    result = {'message': f"✅ You are up to date (v{current_version})", 'status': 'up_to_date'}
                else:
                    result = {'message': f"Current version: {current_version} (unable to check for updates)", 'status': 'error'}
        except Exception as e:
            LOGGER.debug(f"[Stream-Mapparr] Error in version check: {e}")
            result = {'message': f"Current version: {current_version} (update check failed)", 'status': 'error'}
        return result

    def _get_latest_version(self, owner, repo):
        """Helper to fetch latest version tag from GitHub"""
        try:
            url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
            req = urllib.request.Request(url, headers={'User-Agent': 'Dispatcharr-Plugin'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                return data.get('tag_name', '').lstrip('v')
        except Exception:
            return None

    def _get_channel_databases(self):
        """Scan for channel database files and return metadata for each."""
        plugin_dir = os.path.dirname(__file__)
        databases = []
        try:
            from glob import glob
            pattern = os.path.join(plugin_dir, '*_channels.json')
            channel_files = sorted(glob(pattern))
            for channel_file in channel_files:
                try:
                    filename = os.path.basename(channel_file)
                    country_code = filename.split('_')[0].upper()
                    with open(channel_file, 'r', encoding='utf-8') as f:
                        file_data = json.load(f)
                    if isinstance(file_data, dict) and 'country_code' in file_data:
                        country_name = file_data.get('country_name', filename)
                        version = file_data.get('version', '')
                        label = f"{country_name} (v{version})" if version else country_name
                    else:
                        label = filename
                    default = (country_code == 'US')
                    databases.append({'id': country_code, 'label': label, 'default': default, 'file_path': channel_file, 'filename': filename})
                except Exception as e:
                    LOGGER.warning(f"[Stream-Mapparr] Error reading database file {channel_file}: {e}")
                    continue
            if len(databases) == 1:
                databases[0]['default'] = True
        except Exception as e:
            LOGGER.error(f"[Stream-Mapparr] Error scanning for channel databases: {e}")
        return databases

    def _initialize_fuzzy_matcher(self, match_threshold=85):
        """Initialize the fuzzy matcher with configured threshold."""
        if self.fuzzy_matcher is None:
            try:
                plugin_dir = os.path.dirname(__file__)
                self.fuzzy_matcher = FuzzyMatcher(
                    plugin_dir=plugin_dir,
                    match_threshold=match_threshold,
                    logger=LOGGER
                )
                LOGGER.debug(f"[Stream-Mapparr] Initialized FuzzyMatcher with threshold: {match_threshold}")
            except Exception as e:
                LOGGER.warning(f"[Stream-Mapparr] Failed to initialize FuzzyMatcher: {e}")
                self.fuzzy_matcher = None

    def get_or_refresh_api_token(self, settings, logger):
        """Get API token from cache or refresh if expired."""
        if self.api_token and self.token_expiration and self.token_expiration > datetime.now():
            logger.debug("[Stream-Mapparr] Using cached API token.")
            return self.api_token, None

        logger.debug("[Stream-Mapparr] API token is expired or not found, getting a new one.")
        token, error = self._get_api_token(settings, logger)
        if token:
            self.api_token = token
            self.token_expiration = datetime.now() + timedelta(minutes=PluginConfig.API_TOKEN_CACHE_DURATION)
            logger.debug(f"[Stream-Mapparr] API token cached for {PluginConfig.API_TOKEN_CACHE_DURATION} minutes.")

        return token, error

    def _get_api_token(self, settings, logger):
        """Get an API access token using username and password."""
        dispatcharr_url = settings.get("dispatcharr_url") or ""
        dispatcharr_url = dispatcharr_url.strip().rstrip('/') if dispatcharr_url else ""
        username = settings.get("dispatcharr_username") or ""
        password = settings.get("dispatcharr_password") or ""

        if not all([dispatcharr_url, username, password]):
            return None, "Dispatcharr URL, Username, and Password must be configured in the plugin settings."

        try:
            url = f"{dispatcharr_url}/api/accounts/token/"
            payload = {"username": username, "password": password}

            logger.debug(f"[Stream-Mapparr] Attempting to authenticate with Dispatcharr at: {url}")
            response = requests.post(url, json=payload, timeout=15)

            if response.status_code == 401:
                logger.error("[Stream-Mapparr] Authentication failed - invalid credentials")
                return None, "Authentication failed. Please check your username and password in the plugin settings."
            elif response.status_code == 404:
                logger.error(f"[Stream-Mapparr] API endpoint not found - check Dispatcharr URL: {dispatcharr_url}")
                return None, f"API endpoint not found. Please verify your Dispatcharr URL: {dispatcharr_url}"
            elif response.status_code >= 500:
                logger.error(f"[Stream-Mapparr] Server error from Dispatcharr: {response.status_code}")
                return None, f"Dispatcharr server error ({response.status_code}). Please check if Dispatcharr is running properly."

            response.raise_for_status()
            token_data = response.json()
            access_token = token_data.get("access")

            if not access_token:
                logger.error("[Stream-Mapparr] No access token returned from API")
                return None, "Login successful, but no access token was returned by the API."

            logger.debug("[Stream-Mapparr] Successfully obtained API access token")
            return access_token, None

        except requests.exceptions.ConnectionError as e:
            logger.error(f"[Stream-Mapparr] Connection error: {e}")
            return None, f"Unable to connect to Dispatcharr at {dispatcharr_url}. Please check the URL and ensure Dispatcharr is running."
        except requests.exceptions.Timeout as e:
            logger.error(f"[Stream-Mapparr] Request timeout: {e}")
            return None, "Request timed out while connecting to Dispatcharr. Please check your network connection."
        except requests.RequestException as e:
            logger.error(f"[Stream-Mapparr] Request error: {e}")
            return None, f"Network error occurred while authenticating: {e}"
        except json.JSONDecodeError as e:
            logger.error(f"[Stream-Mapparr] Invalid JSON response: {e}")
            return None, "Invalid response from Dispatcharr API. Please check if the URL is correct."
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Unexpected error during authentication: {e}")
            return None, f"Unexpected error during authentication: {e}"

    def _get_api_data(self, endpoint, token, settings, logger, limiter=None):
        """Helper to perform GET requests to the Dispatcharr API with rate limiting support."""
        dispatcharr_url = settings.get("dispatcharr_url") or ""
        dispatcharr_url = dispatcharr_url.strip().rstrip('/') if dispatcharr_url else ""
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

        try:
            if limiter: limiter.wait()
            logger.debug(f"[Stream-Mapparr] Making API request to: {endpoint}")
            response = requests.get(url, headers=headers, timeout=PluginConfig.API_REQUEST_TIMEOUT)

            # --- Smart Rate Limiter Logic ---
            if limiter:
                if response.ok:
                    limiter.report_success()
                else:
                    limiter.report_error(response.status_code)
            # --------------------------------

            if response.status_code == 401:
                logger.error("[Stream-Mapparr] API token expired or invalid, attempting to refresh.")
                self.api_token = None # Invalidate token
                new_token, error = self.get_or_refresh_api_token(settings, logger)
                if error:
                    raise Exception("Failed to refresh API token.")

                # Retry request with new token
                headers['Authorization'] = f'Bearer {new_token}'
                if limiter: limiter.wait() # Wait before retry
                response = requests.get(url, headers=headers, timeout=PluginConfig.API_REQUEST_TIMEOUT)

                if limiter:
                    if response.ok: limiter.report_success()
                    else: limiter.report_error(response.status_code)

            if response.status_code == 403:
                logger.error("[Stream-Mapparr] API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"[Stream-Mapparr] API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")

            response.raise_for_status()

            # Check if response has content before trying to parse JSON
            if not response.text or response.text.strip() == '':
                logger.warning(f"[Stream-Mapparr] API returned empty response for {endpoint}")
                return []

            try:
                json_data = response.json()
            except json.JSONDecodeError as e:
                logger.error(f"[Stream-Mapparr] Invalid JSON response for {endpoint}: {e}")
                logger.debug(f"[Stream-Mapparr] Response content: {response.text[:200]}")
                raise Exception(f"API returned invalid JSON: {e}")

            if isinstance(json_data, dict):
                return json_data.get('results', json_data)
            elif isinstance(json_data, list):
                return json_data
            return []

        except requests.exceptions.RequestException as e:
            logger.error(f"[Stream-Mapparr] API request failed for {endpoint}: {e}")
            raise Exception(f"API request failed: {e}")

    def _patch_api_data(self, endpoint, token, payload, settings, logger, limiter=None):
        """Helper to perform PATCH requests to the Dispatcharr API with rate limiting support."""
        dispatcharr_url = settings.get("dispatcharr_url") or ""
        dispatcharr_url = dispatcharr_url.strip().rstrip('/') if dispatcharr_url else ""
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

        try:
            if limiter: limiter.wait()
            logger.debug(f"[Stream-Mapparr] Making API PATCH request to: {endpoint}")
            response = requests.patch(url, headers=headers, json=payload, timeout=60)

            # --- Smart Rate Limiter Logic ---
            if limiter:
                if response.ok:
                    limiter.report_success()
                else:
                    limiter.report_error(response.status_code)
            # --------------------------------

            if response.status_code == 401:
                logger.error("[Stream-Mapparr] API token expired or invalid, attempting to refresh.")
                self.api_token = None # Invalidate token
                new_token, error = self.get_or_refresh_api_token(settings, logger)
                if error:
                    raise Exception("Failed to refresh API token.")

                # Retry request with new token
                headers['Authorization'] = f'Bearer {new_token}'
                if limiter: limiter.wait()
                response = requests.patch(url, headers=headers, json=payload, timeout=60)
                if limiter:
                    if response.ok: limiter.report_success()
                    else: limiter.report_error(response.status_code)

            if response.status_code == 403:
                logger.error("[Stream-Mapparr] API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"[Stream-Mapparr] API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")

            response.raise_for_status()

            # Check if response has content before trying to parse JSON
            if not response.text or response.text.strip() == '':
                logger.warning(f"[Stream-Mapparr] API returned empty response for {endpoint}")
                return {}

            try:
                return response.json()
            except json.JSONDecodeError as e:
                logger.error(f"[Stream-Mapparr] Invalid JSON response for {endpoint}: {e}")
                logger.debug(f"[Stream-Mapparr] Response content: {response.text[:200]}")
                raise Exception(f"API returned invalid JSON: {e}")

        except requests.exceptions.RequestException as e:
            logger.error(f"[Stream-Mapparr] API PATCH request failed for {endpoint}: {e}")
            raise Exception(f"API PATCH request failed: {e}")

    def _post_api_data(self, endpoint, token, payload, settings, logger):
        """Helper to perform POST requests to the Dispatcharr API."""
        dispatcharr_url = settings.get("dispatcharr_url") or ""
        dispatcharr_url = dispatcharr_url.strip().rstrip('/') if dispatcharr_url else ""
        url = f"{dispatcharr_url}{endpoint}"
        headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}

        try:
            logger.debug(f"[Stream-Mapparr] Making API POST request to: {endpoint}")
            response = requests.post(url, headers=headers, json=payload, timeout=PluginConfig.API_REQUEST_TIMEOUT)

            if response.status_code == 401:
                logger.error("[Stream-Mapparr] API token expired or invalid, attempting to refresh.")
                self.api_token = None # Invalidate token
                new_token, error = self.get_or_refresh_api_token(settings, logger)
                if error:
                    raise Exception("Failed to refresh API token.")

                # Retry request with new token
                headers['Authorization'] = f'Bearer {new_token}'
                response = requests.post(url, headers=headers, json=payload, timeout=PluginConfig.API_REQUEST_TIMEOUT)

            if response.status_code == 403:
                logger.error("[Stream-Mapparr] API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"[Stream-Mapparr] API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")

            response.raise_for_status()

            # Check if response has content before trying to parse JSON
            if not response.text or response.text.strip() == '':
                logger.warning(f"[Stream-Mapparr] API returned empty response for {endpoint}")
                return {}

            try:
                return response.json()
            except json.JSONDecodeError as e:
                logger.error(f"[Stream-Mapparr] Invalid JSON response for {endpoint}: {e}")
                logger.debug(f"[Stream-Mapparr] Response content: {response.text[:200]}")
                raise Exception(f"API returned invalid JSON: {e}")

        except requests.exceptions.RequestException as e:
            logger.error(f"[Stream-Mapparr] API POST request failed for {endpoint}: {e}")
            raise Exception(f"API POST request failed: {e}")

    def _delete_api_data(self, endpoint, token, settings, logger, limiter=None):
        """DELETE request to Dispatcharr API with automatic token refresh.
        
        Args:
            endpoint: API endpoint (e.g., "/api/channels/channel-streams/123/")
            token: Bearer token for authentication
            settings: Plugin settings dict
            logger: Logger instance
            limiter: Optional SmartRateLimiter instance
            
        Returns:
            bool: True if successful, False otherwise
        """
        dispatcharr_url = settings.get("dispatcharr_url", PluginConfig.DEFAULT_DISPATCHARR_URL)
        if not dispatcharr_url:
            logger.error("[Stream-Mapparr] Dispatcharr URL not configured")
            return False
        
        url = f"{dispatcharr_url.rstrip('/')}{endpoint}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        try:
            if limiter:
                limiter.wait()
            
            response = requests.delete(url, headers=headers, timeout=PluginConfig.API_REQUEST_TIMEOUT)
            
            # Handle 401 (token expired) - refresh and retry once
            if response.status_code == 401:
                logger.info("[Stream-Mapparr] Token expired during DELETE, refreshing...")
                new_token = self._get_api_token(settings, logger)
                if new_token:
                    headers["Authorization"] = f"Bearer {new_token}"
                    response = requests.delete(url, headers=headers, timeout=PluginConfig.API_REQUEST_TIMEOUT)
            
            if limiter:
                if response.status_code == 200 or response.status_code == 204:
                    limiter.report_success()
                else:
                    limiter.report_error(response.status_code)
            
            if response.status_code in [200, 204]:
                return True
            else:
                logger.error(f"[Stream-Mapparr] DELETE {endpoint} failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error in DELETE {endpoint}: {str(e)}")
            return False

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
                logger.debug("[Stream-Mapparr] Frontend refresh triggered via WebSocket")
                return True
        except Exception as e:
            logger.warning(f"[Stream-Mapparr] Could not trigger frontend refresh: {e}")
        return False

    @staticmethod
    def _parse_tags(tags_str):
        """Parse comma-separated tags with support for quoted strings."""
        if not tags_str or not tags_str.strip():
            return []

        tags = []
        current_tag = []
        in_quote = None  # None, '"', or "'"
        i = 0

        while i < len(tags_str):
            char = tags_str[i]

            # Handle quote start/end
            if char in ('"', "'") and (in_quote is None or in_quote == char):
                if in_quote is None:
                    in_quote = char  # Start quote
                else:
                    in_quote = None  # End quote
                i += 1
                continue

            # Handle comma (separator) outside of quotes
            if char == ',' and in_quote is None:
                tag = ''.join(current_tag)
                tag = tag.strip()
                if tag:
                    tags.append(tag)
                current_tag = []
                i += 1
                continue

            # Regular character
            current_tag.append(char)
            i += 1

        # Add final tag
        if current_tag or in_quote is not None:
            tag = ''.join(current_tag)
            if in_quote is None:
                tag = tag.strip()
            if tag:
                tags.append(tag)

        return tags

    def _clean_channel_name(self, name, ignore_tags=None, ignore_quality=True, ignore_regional=True,
                           ignore_geographic=True, ignore_misc=True, remove_cinemax=False, remove_country_prefix=False):
        """Remove brackets and their contents from channel name for matching, and remove ignore tags."""
        if self.fuzzy_matcher:
            return self.fuzzy_matcher.normalize_name(
                name, ignore_tags,
                ignore_quality=ignore_quality,
                ignore_regional=ignore_regional,
                ignore_geographic=ignore_geographic,
                ignore_misc=ignore_misc,
                remove_cinemax=remove_cinemax,
                remove_country_prefix=remove_country_prefix
            )

        # Fallback to basic cleaning
        if ignore_tags is None:
            ignore_tags = []

        cleaned = name

        # Remove country code prefix if requested
        if remove_country_prefix:
            quality_tags = {'HD', 'SD', 'FD', 'UHD', 'FHD'}
            prefix_match = re.match(r'^([A-Z]{2,3})[:\s]\s*', cleaned)
            if prefix_match:
                prefix = prefix_match.group(1).upper()
                if prefix not in quality_tags:
                    cleaned = cleaned[len(prefix_match.group(0)):]

        # Remove anything in square brackets or parentheses at the end
        cleaned = re.sub(r'\s*[\[\(][^\[\]\(\)]*[\]\)]\s*$', '', cleaned)
        while True:
            new_cleaned = re.sub(r'\s*[\[\(][^\[\]\(\)]*[\]\)]\s*$', '', cleaned)
            if new_cleaned == cleaned:
                break
            cleaned = new_cleaned

        # Remove ignore tags
        for tag in ignore_tags:
            if '[' in tag or ']' in tag or '(' in tag or ')' in tag:
                escaped_tag = re.escape(tag)
                cleaned = re.sub(r'\s*' + escaped_tag + r'\s*', ' ', cleaned, flags=re.IGNORECASE)
            else:
                cleaned = re.sub(r'\b' + re.escape(tag) + r'\b', '', cleaned, flags=re.IGNORECASE)

        return cleaned.strip()

    def _extract_quality(self, stream_name):
        """Extract quality indicator from stream name."""
        for quality in self.STREAM_QUALITY_ORDER:
            if quality in ["(H)", "(F)", "(D)"]:
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
        """Sort streams by M3U priority first, then by quality using stream_stats (resolution + FPS).
        
        Priority:
        1. M3U source priority (if specified - lower priority number = higher precedence)
        2. Quality tier (High > Medium > Low > Unknown > Dead)
        3. Resolution (higher = better)
        4. FPS (higher = better)
        
        Quality tiers:
        - Tier 0: High quality (>=1280x720 and >=30 FPS)
        - Tier 1: Medium quality (either HD or good FPS)
        - Tier 2: Low quality (below HD and below 30 FPS)
        - Tier 3: Dead streams (0x0 resolution)
        """
        def get_stream_quality_score(stream):
            """Calculate quality score for sorting.
            Returns tuple: (m3u_priority, tier, -resolution_pixels, -fps)
            Lower values = higher priority
            Negative resolution/fps for descending sort
            """
            # Get M3U priority (0 = highest, 999 = lowest/unspecified)
            m3u_priority = stream.get('_m3u_priority', 999)
            
            stats = stream.get('stats', {})
            
            # Check for dead stream (0x0)
            width = stats.get('width', 0)
            height = stats.get('height', 0)
            
            if width == 0 or height == 0:
                # Tier 3: Dead streams (0x0) - lowest priority
                return (m3u_priority, 3, 0, 0)
            
            # Calculate total pixels
            resolution_pixels = width * height
            
            # Get FPS
            fps = stats.get('source_fps', 0)
            
            # Determine quality tier
            is_hd = width >= 1280 and height >= 720
            is_good_fps = fps >= 30
            
            if is_hd and is_good_fps:
                # Tier 0: High quality (HD + good FPS)
                tier = 0
            elif is_hd or is_good_fps:
                # Tier 1: Medium quality (either HD or good FPS)
                tier = 1
            else:
                # Tier 2: Low quality (below HD and below 30 FPS)
                tier = 2
            
            # Return tuple for sorting: (m3u_priority, tier, -pixels, -fps)
            # M3U priority first, then quality tier, then resolution, then FPS
            # Negative values so higher resolution/fps sorts first within tier
            return (m3u_priority, tier, -resolution_pixels, -fps)
        
        # Sort streams by M3U priority first, then quality score
        return sorted(streams, key=get_stream_quality_score)

    def _filter_working_streams(self, streams, logger):
        """
        Filter out dead streams (0x0 resolution) based on IPTV Checker metadata.
        
        Args:
            streams: List of stream dictionaries to filter
            logger: Logger instance for output
            
        Returns:
            List of working streams (excluding dead ones)
        """
        working_streams = []
        dead_count = 0
        no_metadata_count = 0
        
        for stream in streams:
            stream_id = stream['id']
            stream_name = stream.get('name', 'Unknown')
            
            try:
                # Query Stream model for IPTV Checker metadata
                stream_obj = Stream.objects.filter(id=stream_id).first()
                
                if not stream_obj:
                    # Stream not in database - include it (benefit of doubt)
                    working_streams.append(stream)
                    no_metadata_count += 1
                    continue
                
                # Check if stream has been marked dead by IPTV Checker
                # IPTV Checker stores width and height as 0 for dead streams
                width = getattr(stream_obj, 'width', None)
                height = getattr(stream_obj, 'height', None)
                
                # If width or height is None, IPTV Checker hasn't checked this stream yet
                if width is None or height is None:
                    # No metadata yet - include it (benefit of doubt)
                    working_streams.append(stream)
                    no_metadata_count += 1
                    continue
                
                # Check if stream is dead (0x0 resolution)
                if width == 0 or height == 0:
                    # Dead stream - skip it
                    dead_count += 1
                    logger.debug(f"[Stream-Mapparr] Filtered dead stream: '{stream_name}' (ID: {stream_id}, resolution: {width}x{height})")
                    continue
                
                # Working stream - include it
                working_streams.append(stream)
                logger.debug(f"[Stream-Mapparr] Working stream: '{stream_name}' (ID: {stream_id}, resolution: {width}x{height})")
                
            except Exception as e:
                # Error checking stream - include it (benefit of doubt)
                logger.warning(f"[Stream-Mapparr] Error checking stream {stream_id} health: {e}, including stream")
                working_streams.append(stream)
        
        # Log summary
        if dead_count > 0:
            logger.info(f"[Stream-Mapparr] Filtered out {dead_count} dead streams with 0x0 resolution")
        
        if no_metadata_count > 0:
            logger.info(f"[Stream-Mapparr] {no_metadata_count} streams have no IPTV Checker metadata (included by default)")
        
        logger.info(f"[Stream-Mapparr] {len(working_streams)} working streams available for matching")
        
        return working_streams

    def _wait_for_iptv_checker_completion(self, settings, logger, max_wait_hours=None):
        """
        Wait for IPTV Checker to complete if enabled and currently running.
        
        This method monitors the IPTV Checker progress file to determine when it has finished.
        Used by the scheduler to coordinate stream assignment after stream health checks.
        
        Args:
            settings: Plugin settings dictionary
            logger: Logger instance for output
            max_wait_hours: Maximum hours to wait (defaults to setting value)
            
        Returns:
            bool: True if IPTV Checker completed or not running, False if timed out
        """
        wait_enabled = settings.get('wait_for_iptv_checker', PluginConfig.DEFAULT_WAIT_FOR_IPTV_CHECKER)
        if isinstance(wait_enabled, str):
            wait_enabled = wait_enabled.lower() in ('true', 'yes', '1')
        
        if not wait_enabled:
            logger.debug("[Stream-Mapparr] Wait for IPTV Checker disabled, proceeding immediately")
            return True
        
        # Get max wait time from settings or parameter
        if max_wait_hours is None:
            max_wait_hours = settings.get('iptv_checker_max_wait_hours', PluginConfig.DEFAULT_IPTV_CHECKER_MAX_WAIT_HOURS)
            try:
                max_wait_hours = float(max_wait_hours)
            except (ValueError, TypeError):
                max_wait_hours = PluginConfig.DEFAULT_IPTV_CHECKER_MAX_WAIT_HOURS
        
        max_wait_seconds = max_wait_hours * 3600
        check_interval = PluginConfig.IPTV_CHECKER_CHECK_INTERVAL
        progress_file = PluginConfig.IPTV_CHECKER_PROGRESS_FILE
        
        logger.info(f"[Stream-Mapparr] Checking if IPTV Checker is running (max wait: {max_wait_hours} hours)...")
        
        start_time = time.time()
        last_log_time = start_time
        log_interval = 300  # Log status every 5 minutes
        
        while True:
            try:
                # Check if IPTV Checker progress file exists
                if not os.path.exists(progress_file):
                    logger.info("[Stream-Mapparr] IPTV Checker progress file not found - assuming not running")
                    return True
                
                # Read IPTV Checker progress
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                status = progress_data.get('status', 'idle')
                
                if status != 'running':
                    logger.info(f"[Stream-Mapparr] IPTV Checker status: '{status}' - proceeding with stream assignment")
                    return True
                
                # IPTV Checker is still running
                current_time = time.time()
                elapsed_seconds = current_time - start_time
                elapsed_minutes = elapsed_seconds / 60
                
                # Check if we have exceeded max wait time
                if elapsed_seconds >= max_wait_seconds:
                    logger.warning(f"[Stream-Mapparr] IPTV Checker still running after {max_wait_hours} hours - proceeding anyway")
                    return False
                
                # Log status periodically (every 5 minutes)
                if current_time - last_log_time >= log_interval:
                    current = progress_data.get('current', 0)
                    total = progress_data.get('total', 0)
                    percent = (current / total * 100) if total > 0 else 0
                    
                    logger.info(f"[Stream-Mapparr] Waiting for IPTV Checker to complete... "
                               f"({elapsed_minutes:.0f} minutes elapsed, {percent:.0f}% complete)")
                    last_log_time = current_time
                
                # Wait before checking again
                time.sleep(check_interval)
                
            except json.JSONDecodeError as e:
                logger.warning(f"[Stream-Mapparr] Failed to parse IPTV Checker progress file: {e} - proceeding anyway")
                return True
            except Exception as e:
                logger.error(f"[Stream-Mapparr] Error checking IPTV Checker status: {e} - proceeding anyway")
                return True

    def _deduplicate_streams(self, streams):
        """Remove duplicate streams based on stream name.
        
        Keeps the first occurrence of each unique stream name.
        This prevents duplicate stream counts in results.
        
        Args:
            streams: List of stream dictionaries
            
        Returns:
            List of deduplicated stream dictionaries
        """
        seen_names = set()
        deduplicated = []
        
        for stream in streams:
            stream_name = stream.get('name', '')
            if stream_name and stream_name not in seen_names:
                seen_names.add(stream_name)
                deduplicated.append(stream)
        
        return deduplicated

    def _load_channels_data(self, logger, settings=None):
        """Load channel data from enabled *_channels.json files."""
        plugin_dir = os.path.dirname(__file__)
        channels_data = []

        try:
            databases = self._get_channel_databases()

            if not databases:
                logger.warning(f"[Stream-Mapparr] No *_channels.json files found in {plugin_dir}")
                return channels_data

            enabled_databases = []
            for db_info in databases:
                db_id = db_info['id']
                setting_key = f"db_enabled_{db_id}"
                if settings:
                    is_enabled = settings.get(setting_key, db_info['default'])
                    # Handle string boolean values
                    if isinstance(is_enabled, str):
                        is_enabled = is_enabled.lower() in ('true', 'yes', '1')
                else:
                    is_enabled = db_info['default']

                if is_enabled:
                    enabled_databases.append(db_info)

            if not enabled_databases:
                logger.warning("[Stream-Mapparr] No channel databases are enabled. Please enable at least one database in settings.")
                return channels_data

            for db_info in enabled_databases:
                channel_file = db_info['file_path']
                db_label = db_info['label']
                country_code = db_info['id']

                try:
                    with open(channel_file, 'r', encoding='utf-8') as f:
                        file_data = json.load(f)

                    if isinstance(file_data, dict) and 'channels' in file_data:
                        channels_list = file_data['channels']
                        for channel in channels_list:
                            channel['_country_code'] = country_code
                    elif isinstance(file_data, list):
                        channels_list = file_data
                        for channel in channels_list:
                            channel['_country_code'] = country_code
                    else:
                        logger.error(f"[Stream-Mapparr] Invalid format in {channel_file}")
                        continue

                    channels_data.extend(channels_list)
                    logger.debug(f"[Stream-Mapparr] Loaded {len(channels_list)} channels from {db_label}")

                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Error loading {channel_file}: {e}")

            db_names = [db_info['label'] for db_info in enabled_databases]
            logger.info(f"[Stream-Mapparr] Loaded total of {len(channels_data)} channels from {len(enabled_databases)} enabled database(s): {', '.join(db_names)}")

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error loading channel data files: {e}")

        return channels_data

    def _is_ota_channel(self, channel_info):
        """Check if a channel has callsign (indicating it's an OTA broadcast channel)."""
        if not channel_info:
            return False
        if isinstance(channel_info, str): # Handle string input for backwards compatibility if needed
            return False
        return 'callsign' in channel_info and channel_info['callsign']

    def _extract_ota_info(self, channel_name):
        """Helper to extract OTA callsign if not using the full channel object (deprecated/fallback)"""
        # This is mostly a placeholder if needed for string-based checks
        return None

    def _parse_callsign(self, callsign):
        """Extract clean callsign, removing suffixes after dash."""
        if not callsign:
            return None
        if '-' in callsign:
            callsign = callsign.split('-')[0].strip()
        return callsign.upper()

    def _build_us_callsign_database(self, logger):
        """Build lookup dictionary of US OTA callsigns from US_channels.json.
        
        Returns:
            dict: Callsign database mapping base callsigns to channel info
                  Example: {"WKRG": {"base_name": "WKRG (CBS)", "category": "...", "type": "..."}}
        """
        callsign_db = {}
        
        # Load US_channels.json
        plugin_dir = os.path.dirname(__file__)
        us_channels_path = os.path.join(plugin_dir, 'US_channels.json')
        if not os.path.exists(us_channels_path):
            logger.warning(f"[Stream-Mapparr] US_channels.json not found at {us_channels_path}")
            return callsign_db
        
        try:
            with open(us_channels_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            channels = data.get('channels', [])
            logger.info(f"[Stream-Mapparr] Parsing {len(channels)} entries from US_channels.json")
            
            for channel in channels:
                channel_name = channel.get('channel_name', '')
                
                # Extract callsign from channel_name
                callsign = self._extract_us_callsign(channel_name)
                
                if callsign:
                    # Normalize to base callsign (remove suffixes)
                    base_callsign = self._normalize_us_callsign(callsign)
                    
                    # Store in database (first occurrence becomes base_name)
                    if base_callsign not in callsign_db:
                        callsign_db[base_callsign] = {
                            'base_name': channel_name,
                            'category': channel.get('category', ''),
                            'type': channel.get('type', '')
                        }
            
            logger.info(f"[Stream-Mapparr] Built US callsign database with {len(callsign_db)} unique callsigns")
            return callsign_db
            
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error building US callsign database: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {}

    def _extract_us_callsign(self, channel_name):
        """Extract US callsign from channel name in US_channels.json.
        
        Handles formats:
        - WKRG (CBS) → WKRG
        - WKRG-DT (CBS) → WKRG-DT
        - WKRG-DT2 (ION) → WKRG-DT2
        
        Args:
            channel_name: Channel name from US_channels.json
            
        Returns:
            str: Extracted callsign or None if not found
        """
        if not channel_name:
            return None
        
        # Pattern: Match K/W followed by 3-4 letters, optional suffix
        # Examples: WKRG, WKRG-DT, WKRG-DT2, WABC-TV
        match = re.match(r'^([KW][A-Z]{3,4}(?:-[A-Z]{2}\d?)?)', channel_name, re.IGNORECASE)
        if match:
            callsign = match.group(1).upper()
            # Filter false positives
            if callsign not in ['WEST', 'EAST', 'KIDS', 'WOMEN', 'WILD', 'WORLD']:
                return callsign
        
        return None

    def _normalize_us_callsign(self, callsign):
        """Normalize callsign to base form (remove suffixes and parentheses).
        
        Examples:
        - WKRG-DT → WKRG
        - WKRG-DT2 → WKRG
        - WABC-TV → WABC
        - WKRG → WKRG
        
        Args:
            callsign: Full callsign with potential suffix
            
        Returns:
            str: Base callsign without suffix
        """
        if not callsign:
            return None
        
        # Remove everything in parentheses
        callsign = re.sub(r'\s*\([^)]*\)', '', callsign)
        
        # Remove common suffixes: -TV, -CD, -LP, -DT, -DT2, -DT3, etc., -LD
        base = re.sub(r'-(?:TV|CD|LP|DT\d?|LD)$', '', callsign, flags=re.IGNORECASE)
        
        return base.upper().strip()

    def _match_streams_to_channel(self, channel, all_streams, logger, ignore_tags=None,
                                  ignore_quality=True, ignore_regional=True, ignore_geographic=True,
                                  ignore_misc=True, channels_data=None, filter_dead=False):
        """Find matching streams for a channel using fuzzy matching when available."""
        if ignore_tags is None:
            ignore_tags = []
        if channels_data is None:
            channels_data = []

        # Filter dead streams if enabled (0x0 resolution from IPTV Checker)
        if filter_dead:
            working_streams = self._filter_working_streams(all_streams, logger)
        else:
            working_streams = all_streams

        channel_name = channel['name']
        channel_info = self._get_channel_info_from_json(channel_name, channels_data, logger)
        database_used = channel_info.get('_country_code', 'N/A') if channel_info else 'N/A'
        channel_has_max = 'max' in channel_name.lower()

        cleaned_channel_name = self._clean_channel_name(
            channel_name, ignore_tags, ignore_quality, ignore_regional,
            ignore_geographic, ignore_misc
        )

        if "24/7" in channel_name.lower():
            logger.debug(f"[Stream-Mapparr] Cleaned channel name for matching: {cleaned_channel_name}")

        # Check if this channel has a callsign (OTA broadcast channel)
        if self._is_ota_channel(channel_info):
            callsign = channel_info['callsign']
            logger.debug(f"[Stream-Mapparr] Matching OTA channel: {channel_name} using callsign: {callsign}")

            matching_streams = []
            callsign_pattern = r'\b' + re.escape(callsign) + r'\b'

            for stream in working_streams:
                if re.search(callsign_pattern, stream['name'], re.IGNORECASE):
                    matching_streams.append(stream)

            if matching_streams:
                sorted_streams = self._sort_streams_by_quality(matching_streams)
                sorted_streams = self._deduplicate_streams(sorted_streams)
                cleaned_stream_names = [self._clean_channel_name(
                    s['name'], ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                ) for s in sorted_streams]
                return sorted_streams, cleaned_channel_name, cleaned_stream_names, "Callsign match", database_used

        # Use fuzzy matching if available
        if self.fuzzy_matcher:
            stream_names = [stream['name'] for stream in working_streams]
            matched_stream_name, score, match_type = self.fuzzy_matcher.fuzzy_match(
                channel_name, stream_names, ignore_tags, remove_cinemax=channel_has_max,
                ignore_quality=ignore_quality, ignore_regional=ignore_regional,
                ignore_geographic=ignore_geographic, ignore_misc=ignore_misc
            )

            if matched_stream_name:
                matching_streams = []
                
                # Clean the channel name for comparison
                cleaned_channel_for_matching = self._clean_channel_name(
                    channel_name, ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                )
                
                # Match streams against the CHANNEL name, not just the best-matched stream
                # This allows collecting all streams that are similar to the channel
                for stream in working_streams:
                    cleaned_stream = self._clean_channel_name(
                        stream['name'], ignore_tags, ignore_quality, ignore_regional,
                        ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                    )

                    if not cleaned_stream or len(cleaned_stream) < 2: continue
                    if not cleaned_channel_for_matching or len(cleaned_channel_for_matching) < 2: continue

                    # Check if stream is similar enough to channel using fuzzy matcher's logic
                    stream_lower = cleaned_stream.lower()
                    channel_lower = cleaned_channel_for_matching.lower()
                    
                    # Exact match
                    if stream_lower == channel_lower:
                        matching_streams.append(stream)
                        continue
                    
                    # Substring match: stream contains channel OR channel contains stream
                    if stream_lower in channel_lower or channel_lower in stream_lower:
                        # CRITICAL FIX: Add length ratio requirement to prevent false positives
                        # like "story" matching "history" (story is 5 chars, history is 7 chars)
                        # Require strings to be within 75% of same length for substring match
                        # This ensures substring matches are semantically meaningful
                        length_ratio = min(len(stream_lower), len(channel_lower)) / max(len(stream_lower), len(channel_lower))
                        if length_ratio >= 0.75:
                            # Calculate similarity to ensure it meets threshold
                            similarity = self.fuzzy_matcher.calculate_similarity(stream_lower, channel_lower)
                            if int(similarity * 100) >= self.fuzzy_matcher.match_threshold:
                                matching_streams.append(stream)
                        continue
                    
                    # Token-based matching: check if significant tokens overlap
                    # This catches cases like "ca al jazeera" vs "al jazeera english"
                    # Split into tokens (words)
                    stream_tokens = set(stream_lower.split())
                    channel_tokens = set(channel_lower.split())
                    
                    # CRITICAL FIX: For channels with numeric suffixes (like "Premier Sports 1", "Sky Sports 1"),
                    # we must keep single-digit tokens to prevent false matches between numbered channels.
                    # Detect if channel contains any numbers
                    channel_has_numbers = bool(re.search(r'\d', channel_lower))
                    
                    # Extract numeric tokens from channel name for strict matching
                    channel_number_tokens = {t for t in channel_tokens if t.isdigit()}
                    stream_number_tokens = {t for t in stream_tokens if t.isdigit()}
                    
                    # PREVENT FALSE POSITIVES: If channel contains numbers, stream must also contain matching numbers
                    # This prevents "BBC1" from matching "CBBC", "BBC4" from matching "CBBC", etc.
                    if channel_number_tokens:
                        # Channel has numbers - stream must have at least one matching number
                        if not stream_number_tokens:
                            # Stream has no numbers but channel does - likely false positive, skip
                            continue
                        if not channel_number_tokens & stream_number_tokens:
                            # Stream has numbers but none match channel numbers - skip this stream
                            continue
                    
                    if channel_has_numbers:
                        # Keep ALL tokens including single digits (1, 2, 3, etc.) for numbered channels
                        # This ensures "Premier Sports 1" requires token "1" to match
                        stream_tokens = {t for t in stream_tokens if len(t) >= 1}
                        channel_tokens = {t for t in channel_tokens if len(t) >= 1}
                    else:
                        # For non-numbered channels, remove single-char tokens but keep 2-char tokens like "al"
                        # This prevents matching on noise like single letters
                        stream_tokens = {t for t in stream_tokens if len(t) > 1}
                        channel_tokens = {t for t in channel_tokens if len(t) > 1}
                    
                    # Check if there's significant overlap
                    if stream_tokens and channel_tokens:
                        common_tokens = stream_tokens & channel_tokens
                        overlap_ratio = len(common_tokens) / min(len(stream_tokens), len(channel_tokens))
                        
                        # HYBRID APPROACH: Adjust matching strictness based on threshold
                        # At high thresholds (90%+): Use strict matching (only all channel tokens present)
                        # At lower thresholds (<90%): Use permissive matching (sufficient overlap OR all channel tokens)
                        # This prevents false matches like "Premier Sports 1" matching "Premier Sports 2" at 85%
                        # while still allowing flexibility at lower thresholds
                        all_channel_tokens_present = channel_tokens.issubset(stream_tokens)
                        
                        if self.fuzzy_matcher.match_threshold >= 90:
                            # Strict mode: Only match if ALL channel tokens are present in stream
                            # This ensures "Premier Sports 1" only matches streams containing "premier", "sports", AND "1"
                            should_check_similarity = all_channel_tokens_present
                        else:
                            # Permissive mode: Match if sufficient overlap OR all channel tokens present
                            min_tokens_needed = min(len(stream_tokens), len(channel_tokens))
                            has_sufficient_overlap = len(common_tokens) >= min_tokens_needed or overlap_ratio >= 0.75
                            should_check_similarity = has_sufficient_overlap or all_channel_tokens_present
                        
                        if should_check_similarity:
                            # Calculate full string similarity
                            similarity = self.fuzzy_matcher.calculate_similarity(stream_lower, channel_lower)
                            if int(similarity * 100) >= self.fuzzy_matcher.match_threshold:
                                matching_streams.append(stream)

                if matching_streams:
                    sorted_streams = self._sort_streams_by_quality(matching_streams)
                    sorted_streams = self._deduplicate_streams(sorted_streams)
                    cleaned_stream_names = [self._clean_channel_name(
                        s['name'], ignore_tags, ignore_quality, ignore_regional,
                        ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                    ) for s in sorted_streams]
                    return sorted_streams, cleaned_channel_name, cleaned_stream_names, f"Fuzzy match ({match_type}, score: {score})", database_used

            return [], cleaned_channel_name, [], "No fuzzy match", database_used

        # Fallback to basic substring matching if fuzzy matcher unavailable
        matching_streams = []

        if not working_streams:
            return [], cleaned_channel_name, [], "No streams available", database_used

        # Try exact channel name matching from JSON first
        if channel_info and channel_info.get('channel_name'):
            json_channel_name = channel_info['channel_name']
            for stream in working_streams:
                cleaned_stream_name = self._clean_channel_name(
                    stream['name'], ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                )
                if not cleaned_stream_name or len(cleaned_stream_name) < 2: continue
                if not cleaned_channel_name or len(cleaned_channel_name) < 2: continue

                if cleaned_stream_name.lower() == cleaned_channel_name.lower():
                    matching_streams.append(stream)

            if matching_streams:
                sorted_streams = self._sort_streams_by_quality(matching_streams)
                sorted_streams = self._deduplicate_streams(sorted_streams)
                cleaned_stream_names = [self._clean_channel_name(
                    s['name'], ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                ) for s in sorted_streams]
                return sorted_streams, cleaned_channel_name, cleaned_stream_names, "Exact match (channels.json)", database_used

        # Fallback to basic substring matching
        for stream in working_streams:
            cleaned_stream_name = self._clean_channel_name(
                stream['name'], ignore_tags, ignore_quality, ignore_regional,
                ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
            )
            if not cleaned_stream_name or len(cleaned_stream_name) < 2: continue
            if not cleaned_channel_name or len(cleaned_channel_name) < 2: continue

            if cleaned_channel_name.lower() in cleaned_stream_name.lower() or cleaned_stream_name.lower() in cleaned_channel_name.lower():
                matching_streams.append(stream)

        if matching_streams:
            sorted_streams = self._sort_streams_by_quality(matching_streams)
            sorted_streams = self._deduplicate_streams(sorted_streams)
            cleaned_stream_names = [self._clean_channel_name(
                s['name'], ignore_tags, ignore_quality, ignore_regional,
                ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
            ) for s in sorted_streams]
            return sorted_streams, cleaned_channel_name, cleaned_stream_names, "Basic substring match", database_used

        return [], cleaned_channel_name, [], "No match", database_used

    def _get_matches_at_thresholds(self, channel, all_streams, logger, ignore_tags, ignore_quality, 
                                   ignore_regional, ignore_geographic, ignore_misc, channels_data, 
                                   current_threshold):
        """Get streams that match at different threshold levels.
        
        Returns a dict with threshold levels as keys and matched streams as values.
        """
        results = {}
        thresholds_to_test = []
        
        # Generate threshold steps going down from current
        test_threshold = current_threshold
        while test_threshold >= 65:
            thresholds_to_test.append(test_threshold)
            test_threshold -= 5
        
        # Ensure we test at least down to 70 if current is higher
        if 70 not in thresholds_to_test and current_threshold > 70:
            thresholds_to_test.append(70)
            thresholds_to_test.sort(reverse=True)
        
        channel_name = channel['name']
        channel_info = self._get_channel_info_from_json(channel_name, channels_data, logger)
        channel_has_max = 'max' in channel_name.lower()
        
        # For OTA channels, callsign matching doesn't use threshold
        if self._is_ota_channel(channel_info):
            callsign = channel_info['callsign']
            callsign_pattern = r'\b' + re.escape(callsign) + r'\b'
            matching_streams = []
            
            for stream in all_streams:
                if re.search(callsign_pattern, stream['name'], re.IGNORECASE):
                    matching_streams.append(stream)
            
            if matching_streams:
                sorted_streams = self._sort_streams_by_quality(matching_streams)
                sorted_streams = self._deduplicate_streams(sorted_streams)
                results[f"callsign_{current_threshold}"] = {
                    'streams': sorted_streams,
                    'match_type': 'Callsign match'
                }
            return results
        
        # For non-OTA channels, test each threshold
        for threshold in thresholds_to_test:
            if not self.fuzzy_matcher:
                continue
                
            # Temporarily set the threshold
            original_threshold = self.fuzzy_matcher.match_threshold
            self.fuzzy_matcher.match_threshold = threshold
            
            try:
                stream_names = [stream['name'] for stream in all_streams]
                matched_stream_name, score, match_type = self.fuzzy_matcher.fuzzy_match(
                    channel_name, stream_names, ignore_tags, remove_cinemax=channel_has_max,
                    ignore_quality=ignore_quality, ignore_regional=ignore_regional,
                    ignore_geographic=ignore_geographic, ignore_misc=ignore_misc
                )
                
                if matched_stream_name:
                    cleaned_channel_name = self._clean_channel_name(
                        channel_name, ignore_tags, ignore_quality, ignore_regional,
                        ignore_geographic, ignore_misc
                    )
                    cleaned_matched = self._clean_channel_name(
                        matched_stream_name, ignore_tags, ignore_quality, ignore_regional,
                        ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                    )
                    
                    matching_streams = []
                    for stream in all_streams:
                        cleaned_stream = self._clean_channel_name(
                            stream['name'], ignore_tags, ignore_quality, ignore_regional,
                            ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                        )
                        
                        if not cleaned_stream or len(cleaned_stream) < 2:
                            continue
                        if not cleaned_matched or len(cleaned_matched) < 2:
                            continue
                        
                        if cleaned_stream.lower() == cleaned_matched.lower():
                            matching_streams.append(stream)
                    
                    if matching_streams:
                        sorted_streams = self._sort_streams_by_quality(matching_streams)
                        sorted_streams = self._deduplicate_streams(sorted_streams)
                        results[threshold] = {
                            'streams': sorted_streams,
                            'match_type': match_type,
                            'score': score
                        }
            finally:
                # Restore original threshold
                self.fuzzy_matcher.match_threshold = original_threshold
        
        return results

    def _send_progress_update(self, action_id, status, progress, message, context=None):
        """Send WebSocket progress update to frontend as a notification.
        
        Sends completion notifications via WebSocket. Uses a notification pattern
        that works with Dispatcharr's frontend notification system.
        
        Only sends for completion (100% or success/error) to avoid spam.
        """
        try:
            from core.utils import send_websocket_update
            
            # Only notify on completion
            is_complete = progress >= 100 or status in ('success', 'completed', 'error')
            
            if not is_complete:
                LOGGER.debug(f"[Stream-Mapparr] Progress: {action_id} - {progress}% - {message}")
                return
            
            is_success = status in ('success', 'completed')

            # Use standard notification format that frontend already handles
            notification_data = {
                'type': 'notification',  # Standard notification type
                'level': 'success' if is_success else 'error',
                'message': message,
                'title': f'Stream-Mapparr: {action_id.replace("_", " ").title()}',
                'plugin': 'stream-mapparr',
                'action': action_id,
            }

            # Log notification prominently so user can see in logs
            log_level = LOGGER.info if is_success else LOGGER.error
            log_level(f"[Stream-Mapparr] ✅ {action_id.replace('_', ' ').upper()} COMPLETED: {message}")

            LOGGER.debug(f"[Stream-Mapparr] Sending WebSocket notification: {notification_data}")
            send_websocket_update('updates', 'update', notification_data)
            
        except Exception as e:
            LOGGER.warning(f"[Stream-Mapparr] Failed to send notification: {e}")

    def _check_operation_lock(self, logger):
        """
        Check if an operation is currently running.
        Returns (is_locked, lock_info) where lock_info contains action name and start time.
        Auto-expires locks older than configured timeout.
        Verifies process is actually running to handle container restarts.
        """
        lock_file = PluginConfig.OPERATION_LOCK_FILE

        if not os.path.exists(lock_file):
            return False, None

        try:
            with open(lock_file, 'r') as f:
                lock_data = json.load(f)

            action_name = lock_data.get('action', 'unknown')
            lock_time_str = lock_data.get('start_time')
            lock_pid = lock_data.get('pid')

            if lock_time_str:
                lock_time = datetime.fromisoformat(lock_time_str)
                age_minutes = (datetime.now() - lock_time).total_seconds() / 60

                # Auto-expire stale locks
                if age_minutes > PluginConfig.OPERATION_LOCK_TIMEOUT_MINUTES:
                    logger.warning(f"[Stream-Mapparr] Found stale lock from {action_name} ({age_minutes:.1f} min old), auto-removing")
                    os.remove(lock_file)
                    return False, None

                # Check if the process is actually still running (handles container restarts)
                if lock_pid:
                    try:
                        # Check if process exists by sending signal 0 (doesn't actually kill)
                        os.kill(lock_pid, 0)
                        # Process exists, lock is valid
                    except (OSError, ProcessLookupError):
                        # Process doesn't exist - lock is orphaned
                        logger.warning(f"[Stream-Mapparr] Found orphaned lock from {action_name} (process {lock_pid} no longer running), removing")
                        os.remove(lock_file)
                        return False, None

                return True, {
                    'action': action_name,
                    'start_time': lock_time,
                    'age_minutes': age_minutes
                }
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error reading lock file: {e}")
            # If lock file is corrupt, remove it
            try:
                os.remove(lock_file)
            except:
                pass
            return False, None

        return False, None

    def _acquire_operation_lock(self, action_name, logger):
        """
        Acquire operation lock. Returns True if acquired, False if already locked.
        """
        is_locked, lock_info = self._check_operation_lock(logger)

        if is_locked:
            logger.warning(f"[Stream-Mapparr] Cannot start {action_name} - {lock_info['action']} is already running ({lock_info['age_minutes']:.1f} min)")
            return False

        try:
            lock_data = {
                'action': action_name,
                'start_time': datetime.now().isoformat(),
                'pid': os.getpid()
            }
            with open(PluginConfig.OPERATION_LOCK_FILE, 'w') as f:
                json.dump(lock_data, f, indent=2)

            logger.info(f"[Stream-Mapparr] Lock acquired for {action_name}")
            return True
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Failed to acquire lock: {e}")
            return False

    def _release_operation_lock(self, logger):
        """Release operation lock."""
        try:
            if os.path.exists(PluginConfig.OPERATION_LOCK_FILE):
                os.remove(PluginConfig.OPERATION_LOCK_FILE)
                logger.debug("[Stream-Mapparr] Lock released")
        except Exception as e:
            logger.warning(f"[Stream-Mapparr] Failed to release lock: {e}")

    def _get_channel_info_from_json(self, channel_name, channels_data, logger):
        """Find channel info from channels.json by matching channel name."""
        for entry in channels_data:
            if entry.get('channel_name', '') == channel_name:
                return entry

        channel_name_lower = channel_name.lower()
        for entry in channels_data:
            if entry.get('channel_name', '').lower() == channel_name_lower:
                return entry
        return None

    def save_settings(self, settings, context):
        """Save settings and sync schedules automatically"""
        try:
            LOGGER.debug(f"[Stream-Mapparr] Saving settings with keys: {list(settings.keys())}")

            # Get timezone and schedule settings
            user_timezone = settings.get("timezone") or PluginConfig.DEFAULT_TIMEZONE
            enabled = settings.get("schedule_enabled", False)
            if isinstance(enabled, str):
                enabled = enabled.lower() in ('true', '1', 'yes', 'on')

            cron_schedule = settings.get("schedule_cron") or ""
            cron_schedule = cron_schedule.strip() if cron_schedule else ""

            LOGGER.debug(f"[Stream-Mapparr] Schedule settings: enabled={enabled}, cron='{cron_schedule}', tz={user_timezone}")

            # Sync the schedule
            if enabled and cron_schedule:
                if not self._validate_cron(cron_schedule):
                    return {
                        "success": False,
                        "message": f"Invalid cron expression: {cron_schedule}"
                    }
                self._create_or_update_schedule(cron_schedule, user_timezone, settings)
                message = f"✅ Settings saved!\n📅 Schedule activated: {cron_schedule} ({user_timezone})"
            else:
                self._delete_schedule()
                if enabled:
                    message = "✅ Settings saved!\n⚠️ Schedule enabled but no cron expression configured"
                else:
                    message = "✅ Settings saved!\nℹ️ Scheduled runs disabled"

            return {
                "success": True,
                "message": message
            }

        except Exception as e:
            LOGGER.error(f"[Stream-Mapparr] Error saving settings: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Error: {str(e)}"
            }

    def run(self, action, settings, context=None):
        """Execute plugin action."""
        try:
            logger = LOGGER

            # If settings is empty but context has settings, use context settings
            if context and isinstance(context, dict) and not settings:
                if 'settings' in context:
                    settings = context['settings']

            # Initialize fuzzy matcher with configured threshold
            match_threshold = settings.get("fuzzy_match_threshold", PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD)
            try:
                match_threshold = int(match_threshold)
            except (ValueError, TypeError):
                match_threshold = PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD

            self._initialize_fuzzy_matcher(match_threshold)

            # Actions that should run in background to avoid timeout
            # Note: load_process_channels is internal-only (called by preview/add actions)
            background_actions = {
                "load_process_channels": self.load_process_channels_action,
                "preview_changes": self.preview_changes_action,
                "add_streams_to_channels": self.add_streams_to_channels_action,
                "sort_streams": self.sort_streams_action,
                "manage_channel_visibility": self.manage_channel_visibility_action,
                "match_us_ota_only": self.match_us_ota_only_action,
            }
            
            # Actions that run immediately (synchronous)
            immediate_actions = {
                "validate_settings": self.validate_settings_action,
                "update_schedule": self.update_schedule_action,
                "cleanup_periodic_tasks": self.cleanup_periodic_tasks_action,
                "clear_csv_exports": self.clear_csv_exports_action,
                "clear_operation_lock": self.clear_operation_lock_action,
            }

            if action in background_actions:
                # Check if another operation is already running (only for long operations)
                if action in ['preview_changes', 'add_streams_to_channels', 'sort_streams', 'match_us_ota_only']:
                    is_locked, lock_info = self._check_operation_lock(logger)
                    if is_locked:
                        action_label = action.replace("_", " ").title()
                        running_action = lock_info['action'].replace("_", " ").title()
                        age_min = lock_info['age_minutes']
                        return {
                            'status': 'error',
                            'message': (
                                f'❌ Cannot start {action_label}\n\n'
                                f'Another operation is already running:\n'
                                f'  • {running_action}\n'
                                f'  • Started {age_min:.1f} minutes ago\n\n'
                                f'⏳ Please wait for it to complete before starting another operation.\n\n'
                                f'📋 Check Docker logs for completion:\n'
                                f'   docker logs -f dispatcharr | grep "COMPLETED"'
                            )
                        }

                # Run in background thread to prevent timeout/broken pipe errors
                # Return immediately while operation continues in background
                def background_runner():
                    lock_acquired = False
                    try:
                        # Acquire lock for long-running operations
                        if action in ['preview_changes', 'add_streams_to_channels', 'sort_streams', 'match_us_ota_only']:
                            if not self._acquire_operation_lock(action, logger):
                                logger.error(f"[Stream-Mapparr] Failed to acquire lock for {action}")
                                return
                            lock_acquired = True

                        self._send_progress_update(action, 'running', 0, 'Starting operation...', context)
                        result = background_actions[action](settings, logger, context)

                        if result.get('status') == 'success':
                            self._send_progress_update(action, 'completed', 100,
                                                      result.get('message', 'Operation completed successfully'), context)
                        else:
                            self._send_progress_update(action, 'error', 0,
                                                      result.get('message', 'Operation failed'), context)
                    except Exception as e:
                        logger.error(f"[Stream-Mapparr] Operation failed: {str(e)}")
                        import traceback
                        logger.error(traceback.format_exc())
                        error_msg = f'Error: {str(e)}'
                        self._send_progress_update(action, 'error', 0, error_msg, context)
                    finally:
                        # Always release lock when done
                        if lock_acquired:
                            self._release_operation_lock(logger)

                # Start background thread
                bg_thread = threading.Thread(target=background_runner, name=f"stream-mapparr-{action}", daemon=True)
                bg_thread.start()

                # Return immediately with "started" status
                # Calculate estimated time if processed data exists
                eta_msg = ""
                try:
                    if os.path.exists(self.processed_data_file):
                        with open(self.processed_data_file, 'r') as f:
                            processed_data = json.load(f)
                            channels = processed_data.get('channels', [])
                            if channels:
                                # Group channels to get item count
                                ignore_tags = processed_data.get('ignore_tags', [])
                                channels_data = self._load_channels_data(logger, settings)
                                channel_groups = {}
                                for channel in channels:
                                    channel_info = self._get_channel_info_from_json(channel['name'], channels_data, logger)
                                    if self._is_ota_channel(channel_info):
                                        callsign = channel_info.get('callsign', '')
                                        group_key = f"OTA_{callsign}" if callsign else channel['name']
                                    else:
                                        group_key = self._clean_channel_name(
                                            channel['name'], ignore_tags,
                                            processed_data.get('ignore_quality', True),
                                            processed_data.get('ignore_regional', True),
                                            processed_data.get('ignore_geographic', True),
                                            processed_data.get('ignore_misc', True)
                                        )
                                    if group_key not in channel_groups:
                                        channel_groups[group_key] = []
                                    channel_groups[group_key].append(channel)
                                
                                item_count = len(channel_groups)
                                eta_seconds = item_count * PluginConfig.ESTIMATED_SECONDS_PER_ITEM
                                eta_minutes = int(eta_seconds / 60)
                                eta_msg = f" Estimated ~{eta_minutes}m."
                except Exception as e:
                    logger.debug(f"[Stream-Mapparr] Could not calculate ETA: {e}")
                
                action_label = action.replace("_", " ").title()
                return {
                    'status': 'success',
                    'message': (
                        f'✅ {action_label} started.{eta_msg}\n'
                        f'📋 Monitor: docker logs -f dispatcharr\n'
                        f'🔍 Look for: ✅ {action.replace("_", " ").upper()} COMPLETED'
                    ),
                    'background': True
                }
            
            elif action in immediate_actions:
                # Immediate actions run synchronously and return result
                return immediate_actions[action](settings, logger)
            
            else:
                return {"status": "error", "message": f"Unknown action: {action}"}

        except Exception as e:
            LOGGER.error(f"[Stream-Mapparr] Error in plugin run: {str(e)}")
            import traceback
            LOGGER.error(traceback.format_exc())
            return {"status": "error", "message": str(e)}

    def _validate_plugin_settings(self, settings, logger):
        """Helper method to validate plugin settings."""
        validation_results = []
        has_errors = False
        token = None

        try:
            # 1. Validate API connection and obtain token
            logger.debug("[Stream-Mapparr] Validating API connection...")
            token, error = self.get_or_refresh_api_token(settings, logger)
            if error:
                validation_results.append(f"❌ API Connection: {error}")
                has_errors = True
                return has_errors, validation_results, token
            else:
                validation_results.append("✅ API Connection")

            # 2. Validate profile name exists
            logger.debug("[Stream-Mapparr] Validating profile names...")
            profile_names_str = settings.get("profile_name") or ""
            profile_names_str = profile_names_str.strip() if profile_names_str else ""
            if not profile_names_str:
                validation_results.append("❌ Profile Name: Not configured")
                has_errors = True
            else:
                profile_names = [name.strip() for name in profile_names_str.split(',') if name.strip()]
                profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger)
                available_profile_names = [p.get('name') for p in profiles if 'name' in p]

                missing_profiles = []
                found_profiles = []
                for profile_name in profile_names:
                    found = False
                    for profile in profiles:
                        if profile.get('name', '').lower() == profile_name.lower():
                            found = True
                            found_profiles.append(profile_name)
                            break
                    if not found:
                        missing_profiles.append(profile_name)

                if missing_profiles:
                    validation_results.append(f"❌ Profile Name: '{', '.join(missing_profiles)}' not found")
                    has_errors = True
                else:
                    validation_results.append(f"✅ Profile Name ({len(found_profiles)})")

            # 3. Validate channel groups (if specified)
            logger.debug("[Stream-Mapparr] Validating channel groups...")
            selected_groups_str = settings.get("selected_groups") or ""
            selected_groups_str = selected_groups_str.strip() if selected_groups_str else ""
            
            if selected_groups_str:
                # Groups are specified, validate them
                selected_groups = [g.strip() for g in selected_groups_str.split(',') if g.strip()]
                
                # Fetch all groups from API
                all_groups = []
                page = 1
                while True:
                    try:
                        api_groups = self._get_api_data(f"/api/channels/groups/?page={page}", token, settings, logger)
                    except Exception as e:
                        # If we get an error (e.g., 404 for non-existent page), we've reached the end
                        if page > 1:
                            logger.debug(f"[Stream-Mapparr] No more group pages available (attempted page {page})")
                            break
                        else:
                            # If error on first page, re-raise
                            raise

                    if isinstance(api_groups, dict) and 'results' in api_groups:
                        results = api_groups['results']
                        if not results:
                            logger.debug("[Stream-Mapparr] Reached last page of groups (empty results)")
                            break
                        all_groups.extend(results)
                        if not api_groups.get('next'):
                            break
                        page += 1
                    elif isinstance(api_groups, list):
                        if not api_groups:
                            logger.debug("[Stream-Mapparr] Reached last page of groups (empty results)")
                            break
                        all_groups.extend(api_groups)
                        break
                    else:
                        break
                
                available_group_names = [g.get('name') for g in all_groups if 'name' in g]
                
                missing_groups = []
                found_groups = []
                for group_name in selected_groups:
                    if group_name in available_group_names:
                        found_groups.append(group_name)
                    else:
                        missing_groups.append(group_name)
                
                if missing_groups:
                    validation_results.append(f"❌ Channel Groups: '{', '.join(missing_groups)}' not found")
                    has_errors = True
                else:
                    validation_results.append(f"✅ Channel Groups ({len(found_groups)})")
            else:
                # No groups specified is valid (means all groups)
                validation_results.append("✅ Channel Groups (all)")

            # 4. Validate timezone is not empty
            logger.debug("[Stream-Mapparr] Validating timezone...")
            timezone_str = settings.get("timezone") or PluginConfig.DEFAULT_TIMEZONE
            timezone_str = timezone_str.strip() if timezone_str else PluginConfig.DEFAULT_TIMEZONE
            if not timezone_str:
                validation_results.append("❌ Timezone: Not configured")
                has_errors = True
            else:
                # Validate timezone is valid
                try:
                    import pytz
                    pytz.timezone(timezone_str)
                    validation_results.append(f"✅ Timezone")
                except pytz.exceptions.UnknownTimeZoneError:
                    validation_results.append(f"❌ Timezone: Invalid '{timezone_str}'")
                    has_errors = True

            # 5. Validate at least one channel database is checked
            logger.debug("[Stream-Mapparr] Validating channel databases...")
            databases = self._get_channel_databases()
            
            if not databases:
                validation_results.append("❌ Channel Databases: No files found")
                has_errors = True
            else:
                enabled_databases = []
                for db_info in databases:
                    db_id = db_info['id']
                    setting_key = f"db_enabled_{db_id}"
                    is_enabled = settings.get(setting_key, db_info['default'])
                    
                    # Handle string boolean values
                    if isinstance(is_enabled, str):
                        is_enabled = is_enabled.lower() in ('true', 'yes', '1', 'on')
                    
                    if is_enabled:
                        enabled_databases.append(db_info['label'])
                
                if not enabled_databases:
                    validation_results.append("❌ Channel Databases: None enabled")
                    has_errors = True
                else:
                    validation_results.append(f"✅ Channel Databases ({len(enabled_databases)})")

            return has_errors, validation_results, token

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error validating settings: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            validation_results.append(f"❌ Validation error: {str(e)}")
            has_errors = True
            return has_errors, validation_results, token

    def validate_settings_action(self, settings, logger):
        """Validate all plugin settings including profiles, groups, and API connection."""
        has_errors, validation_results, token = self._validate_plugin_settings(settings, logger)

        if has_errors:
            # Separate errors from successes
            errors = [item for item in validation_results if item.startswith("❌")]
            message = "Validation failed:\n\n" + "\n".join(errors)
            return {"status": "error", "message": message}
        else:
            message = "✅ All settings validated successfully!\n\n" + "\n".join(validation_results)
            return {"status": "success", "message": message}

    def sync_schedules_action(self, settings, logger):
        """Sync schedules from settings"""
        try:
            user_timezone = settings.get("timezone") or PluginConfig.DEFAULT_TIMEZONE
            enabled = settings.get("schedule_enabled", False)
            if isinstance(enabled, str):
                enabled = enabled.lower() in ('true', '1', 'yes', 'on')

            cron_schedule = settings.get("schedule_cron") or ""
            cron_schedule = cron_schedule.strip() if cron_schedule else ""

            logger.debug(f"[Stream-Mapparr] Syncing schedule: enabled={enabled}, schedule='{cron_schedule}', tz={user_timezone}")

            if enabled and cron_schedule:
                if not self._validate_cron(cron_schedule):
                    return {
                        "status": "error",
                        "message": f"Invalid cron expression: {cron_schedule}"
                    }
                self._create_or_update_schedule(cron_schedule, user_timezone, settings)
                return {
                    "status": "success",
                    "message": f"✅ Schedule synced! Cron: {cron_schedule} ({user_timezone})"
                }
            else:
                self._delete_schedule()
                if not enabled:
                    return {
                        "status": "success",
                        "message": "ℹ️ Schedule disabled and removed"
                    }
                else:
                    return {
                        "status": "success",
                        "message": "ℹ️ No cron expression configured"
                    }

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error syncing schedule: {e}", exc_info=True)
            return {"status": "error", "message": f"Error: {str(e)}"}

    def view_schedules_action(self, settings, logger):
        """View active schedule"""
        try:
            user_timezone = settings.get("timezone", PluginConfig.DEFAULT_TIMEZONE)
            logger.debug(f"[Stream-Mapparr] Viewing schedules with timezone: {user_timezone}")

            task_name = "stream_mapparr_scheduled_task"
            task = PeriodicTask.objects.filter(name=task_name, enabled=True).first()

            if task and task.crontab:
                cron = task.crontab
                cron_expr = f"{cron.minute} {cron.hour} {cron.day_of_month} {cron.month_of_year} {cron.day_of_week}"

                # Try to convert back to user's timezone for display
                local_time = self._convert_utc_to_local(cron.minute, cron.hour, user_timezone)

                if local_time:
                    message = f"📅 Stream-Mapparr Schedule:\n  • Cron: {cron_expr} UTC ({local_time} {user_timezone})\n  • Status: Active"
                else:
                    message = f"📅 Stream-Mapparr Schedule:\n  • Cron: {cron_expr} UTC\n  • Status: Active"
            else:
                message = "ℹ️ No active schedule found"

            return {"status": "success", "message": message}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error viewing schedules: {e}", exc_info=True)
            return {"status": "error", "message": f"Error: {str(e)}"}

    def cleanup_schedule_action(self, settings, logger):
        """Remove schedule created by this plugin"""
        try:
            task_name = "stream_mapparr_scheduled_task"
            deleted_count = PeriodicTask.objects.filter(name=task_name).delete()[0]

            if deleted_count > 0:
                message = f"✅ Removed Stream-Mapparr schedule"
            else:
                message = "ℹ️ No schedule found to remove"

            return {"status": "success", "message": message}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error cleaning up schedule: {e}", exc_info=True)
            return {"status": "error", "message": f"Error: {str(e)}"}

    def test_celery_task_action(self, settings, logger):
        """Test if Celery task can be called and check registration"""
        try:
            messages = []

            # Try to import the task
            try:
                from . import tasks
                messages.append("✅ Task module imported successfully")
            except Exception as e:
                messages.append(f"❌ Failed to import task module: {e}")
                return {"status": "error", "message": "\n".join(messages)}

            # Try to call the task directly (non-async)
            try:
                result = tasks.run_scheduled_stream_mapping()
                messages.append("✅ Task executed directly (non-Celery)")
                messages.append(f"   Result: {result.get('status', 'unknown')}")
            except Exception as e:
                messages.append(f"⚠️ Direct execution failed: {e}")

            # Check if Celery can see the task
            try:
                from celery import current_app
                registered_tasks = list(current_app.tasks.keys())
                task_name = 'stream_mapparr.run_scheduled_stream_mapping'

                if task_name in registered_tasks:
                    messages.append(f"✅ Task registered in Celery: {task_name}")
                else:
                    messages.append(f"❌ Task NOT found in Celery registry")
                    messages.append(f"   Looking for: {task_name}")
                    # Show similar tasks
                    similar = [t for t in registered_tasks if 'stream' in t.lower() or 'mapparr' in t.lower()]
                    if similar:
                        messages.append(f"   Similar tasks: {similar}")
            except Exception as e:
                messages.append(f"⚠️ Could not check Celery registry: {e}")

            # Check periodic task in database
            try:
                task_name = "stream_mapparr_scheduled_task"
                task = PeriodicTask.objects.filter(name=task_name).first()
                if task:
                    messages.append(f"✅ Periodic task found in database:")
                    messages.append(f"   Task: {task.task}")
                    messages.append(f"   Enabled: {task.enabled}")
                    if task.crontab:
                        cron = task.crontab
                        messages.append(f"   Schedule: {cron.minute} {cron.hour} {cron.day_of_month} {cron.month_of_year} {cron.day_of_week}")
                else:
                    messages.append(f"ℹ️ No periodic task found in database")
            except Exception as e:
                messages.append(f"⚠️ Error checking database: {e}")

            return {"status": "success", "message": "\n".join(messages)}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error testing task: {e}", exc_info=True)
            return {"status": "error", "message": f"Error: {str(e)}"}

    def load_process_channels_action(self, settings, logger, context=None):
        """Load and process channels from specified profile and groups."""
        try:
            # Create the rate limiter instance once
            limiter = SmartRateLimiter(settings.get("rate_limiting", "none"), logger)

            self._send_progress_update("load_process_channels", 'running', 5, 'Validating settings...', context)
            logger.debug("[Stream-Mapparr] Validating settings before loading channels...")
            has_errors, validation_results, token = self._validate_plugin_settings(settings, logger)

            if has_errors:
                return {"status": "error", "message": "Cannot load channels - validation failed."}

            self._send_progress_update("load_process_channels", 'running', 10, 'Settings validated, loading data...', context)
            logger.info("[Stream-Mapparr] Settings validated successfully, proceeding with channel load...")

            profile_names_str = settings.get("profile_name") or ""
            profile_names_str = profile_names_str.strip() if profile_names_str else ""
            selected_groups_str = settings.get("selected_groups") or ""
            selected_groups_str = selected_groups_str.strip() if selected_groups_str else ""
            selected_stream_groups_str = settings.get("selected_stream_groups") or ""
            selected_stream_groups_str = selected_stream_groups_str.strip() if selected_stream_groups_str else ""
            selected_m3us_str = settings.get("selected_m3us") or ""
            selected_m3us_str = selected_m3us_str.strip() if selected_m3us_str else ""
            ignore_tags_str = settings.get("ignore_tags") or ""
            ignore_tags_str = ignore_tags_str.strip() if ignore_tags_str else ""
            visible_channel_limit_str = settings.get("visible_channel_limit", str(PluginConfig.DEFAULT_VISIBLE_CHANNEL_LIMIT))
            visible_channel_limit = int(visible_channel_limit_str) if visible_channel_limit_str else PluginConfig.DEFAULT_VISIBLE_CHANNEL_LIMIT

            ignore_quality = settings.get("ignore_quality_tags", PluginConfig.DEFAULT_IGNORE_QUALITY_TAGS)
            ignore_regional = settings.get("ignore_regional_tags", PluginConfig.DEFAULT_IGNORE_REGIONAL_TAGS)
            ignore_geographic = settings.get("ignore_geographic_tags", PluginConfig.DEFAULT_IGNORE_GEOGRAPHIC_TAGS)
            ignore_misc = settings.get("ignore_misc_tags", PluginConfig.DEFAULT_IGNORE_MISC_TAGS)

            # Handle boolean string conversions
            if isinstance(ignore_quality, str): ignore_quality = ignore_quality.lower() in ('true', 'yes', '1')
            if isinstance(ignore_regional, str): ignore_regional = ignore_regional.lower() in ('true', 'yes', '1')
            if isinstance(ignore_geographic, str): ignore_geographic = ignore_geographic.lower() in ('true', 'yes', '1')
            if isinstance(ignore_misc, str): ignore_misc = ignore_misc.lower() in ('true', 'yes', '1')

            profile_names = [name.strip() for name in profile_names_str.split(',') if name.strip()]
            ignore_tags = self._parse_tags(ignore_tags_str) if ignore_tags_str else []

            # Fetch profiles with rate limiting
            self._send_progress_update("load_process_channels", 'running', 20, 'Fetching profiles...', context)
            profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger, limiter=limiter)

            target_profiles = []
            profile_ids = []
            for profile_name in profile_names:
                found_profile = None
                for profile in profiles:
                    if profile.get('name', '').lower() == profile_name.lower():
                        found_profile = profile
                        break
                if not found_profile:
                    return {"status": "error", "message": f"Profile '{profile_name}' not found."}
                target_profiles.append(found_profile)
                profile_ids.append(found_profile['id'])

            profile_id = profile_ids[0]

            # Fetch groups with rate limiting
            self._send_progress_update("load_process_channels", 'running', 30, 'Fetching channel groups...', context)
            all_groups = []
            page = 1
            while True:
                try:
                    api_groups = self._get_api_data(f"/api/channels/groups/?page={page}", token, settings, logger, limiter=limiter)
                except Exception as e:
                    # If we get an error (e.g., 404 for non-existent page), we've reached the end
                    if page > 1:
                        logger.debug(f"[Stream-Mapparr] No more group pages available (attempted page {page})")
                        break
                    else:
                        # If error on first page, re-raise
                        raise

                if isinstance(api_groups, dict) and 'results' in api_groups:
                    results = api_groups['results']
                    if not results:
                        logger.debug("[Stream-Mapparr] Reached last page of groups (empty results)")
                        break
                    all_groups.extend(results)
                    if not api_groups.get('next'):
                        break
                    page += 1
                elif isinstance(api_groups, list):
                    if not api_groups:
                        logger.debug("[Stream-Mapparr] Reached last page of groups (empty results)")
                        break
                    all_groups.extend(api_groups)
                    break
                else:
                    break

            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}

            # Fetch stream groups with rate limiting (returns array of group name strings)
            self._send_progress_update("load_process_channels", 'running', 35, 'Fetching stream groups...', context)
            all_stream_groups = []
            try:
                api_stream_groups = self._get_api_data("/api/channels/streams/groups/", token, settings, logger, limiter=limiter)
                if isinstance(api_stream_groups, list):
                    all_stream_groups = api_stream_groups
                    logger.info(f"[Stream-Mapparr] Found {len(all_stream_groups)} stream groups")
                else:
                    logger.warning(f"[Stream-Mapparr] Unexpected stream groups response format: {type(api_stream_groups)}")
            except Exception as e:
                # Stream groups might not be available in this API version
                logger.warning(f"[Stream-Mapparr] Could not fetch stream groups (API may not support this endpoint): {e}")

            # Fetch M3U sources with rate limiting (returns array of M3U account objects)
            self._send_progress_update("load_process_channels", 'running', 37, 'Fetching M3U sources...', context)
            all_m3us = []
            try:
                api_m3us = self._get_api_data("/api/m3u/accounts/", token, settings, logger, limiter=limiter)
                if isinstance(api_m3us, list):
                    all_m3us = api_m3us
                    logger.info(f"[Stream-Mapparr] Found {len(all_m3us)} M3U sources")
                else:
                    logger.warning(f"[Stream-Mapparr] Unexpected M3U sources response format: {type(api_m3us)}")
            except Exception as e:
                # M3U sources might not be available in this API version
                logger.warning(f"[Stream-Mapparr] Could not fetch M3U sources (API may not support this endpoint): {e}")

            m3u_name_to_id = {m['name']: m['id'] for m in all_m3us if 'name' in m and 'id' in m}

            # Fetch channels with rate limiting
            self._send_progress_update("load_process_channels", 'running', 40, 'Fetching channels...', context)
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger, limiter=limiter)

            channels_in_profile = []
            for channel in all_channels:
                channel_id = channel['id']
                is_in_profile = ChannelProfileMembership.objects.filter(
                    channel_id=channel_id,
                    channel_profile_id__in=profile_ids,
                    enabled=True
                ).exists()
                if is_in_profile:
                    channels_in_profile.append(channel)

            if selected_groups_str:
                selected_groups = [g.strip() for g in selected_groups_str.split(',') if g.strip()]
                valid_group_ids = [group_name_to_id[name] for name in selected_groups if name in group_name_to_id]
                if not valid_group_ids:
                    return {"status": "error", "message": "None of the specified groups were found."}

                filtered_channels = [ch for ch in channels_in_profile if ch.get('channel_group_id') in valid_group_ids]
                channels_in_profile = filtered_channels
                group_filter_info = f" in groups: {', '.join(selected_groups)}"
            else:
                selected_groups = []
                group_filter_info = " (all groups)"

            if not channels_in_profile:
                return {"status": "error", "message": f"No channels found in profile."}

            channels_to_process = channels_in_profile

            # Fetch streams with rate limiting
            self._send_progress_update("load_process_channels", 'running', 60, 'Fetching streams (this may take a while)...', context)
            logger.info("[Stream-Mapparr] Fetching all streams from all groups (unlimited)...")
            all_streams_data = []
            page = 1
            while True:
                endpoint = f"/api/channels/streams/?page={page}&page_size=100"

                try:
                    streams_response = self._get_api_data(endpoint, token, settings, logger, limiter=limiter)
                except Exception as e:
                    # If we get an error (e.g., 404 for non-existent page), we've reached the end
                    if page > 1:
                        logger.debug(f"[Stream-Mapparr] No more pages available (attempted page {page})")
                        break
                    else:
                        # If error on first page, re-raise
                        raise

                # Handle both paginated and non-paginated responses
                if isinstance(streams_response, dict) and 'results' in streams_response:
                    results = streams_response['results']

                    # Check if we got empty results
                    if not results:
                        logger.debug("[Stream-Mapparr] Reached last page of streams (empty results)")
                        break

                    all_streams_data.extend(results)
                    logger.debug(f"[Stream-Mapparr] Fetched page {page}: {len(results)} streams (total so far: {len(all_streams_data)})")

                    # Stop if this page had fewer results than page_size (last page)
                    if len(results) < 100:
                        logger.debug("[Stream-Mapparr] Reached last page of streams")
                        break

                    page += 1
                elif isinstance(streams_response, list):
                    # Check if we got empty results
                    if not streams_response:
                        logger.debug("[Stream-Mapparr] Reached last page of streams (empty results)")
                        break

                    # List response - could still be paginated
                    all_streams_data.extend(streams_response)
                    logger.debug(f"[Stream-Mapparr] Fetched page {page}: {len(streams_response)} streams (total so far: {len(all_streams_data)})")

                    # If we got exactly 100 results, there might be more pages
                    if len(streams_response) == 100:
                        page += 1
                    else:
                        logger.debug("[Stream-Mapparr] Reached last page of streams")
                        break
                else:
                    logger.warning("[Stream-Mapparr] Unexpected streams response format")
                    break

            # Filter streams by selected stream groups (uses channel_group field)
            if selected_stream_groups_str:
                selected_stream_groups = [g.strip() for g in selected_stream_groups_str.split(',') if g.strip()]
                valid_stream_group_ids = [group_name_to_id[name] for name in selected_stream_groups if name in group_name_to_id]
                if not valid_stream_group_ids:
                    logger.warning("[Stream-Mapparr] None of the specified stream groups were found. Using all streams.")
                    selected_stream_groups = []
                    stream_group_filter_info = " (all stream groups - specified groups not found)"
                else:
                    # Filter streams by channel_group (which is the group ID)
                    filtered_streams = [s for s in all_streams_data if s.get('channel_group') in valid_stream_group_ids]
                    logger.info(f"[Stream-Mapparr] Filtered streams from {len(all_streams_data)} to {len(filtered_streams)} based on stream groups: {', '.join(selected_stream_groups)}")
                    all_streams_data = filtered_streams
                    stream_group_filter_info = f" in stream groups: {', '.join(selected_stream_groups)}"
            else:
                selected_stream_groups = []
                stream_group_filter_info = " (all stream groups)"

            # Filter streams by selected M3U sources and add priority metadata
            if selected_m3us_str:
                selected_m3us = [m.strip() for m in selected_m3us_str.split(',') if m.strip()]
                valid_m3u_ids = [m3u_name_to_id[name] for name in selected_m3us if name in m3u_name_to_id]
                if not valid_m3u_ids:
                    logger.warning("[Stream-Mapparr] None of the specified M3U sources were found. Using all streams.")
                    selected_m3us = []
                    m3u_filter_info = " (all M3U sources - specified M3Us not found)"
                    # Add default priority to all streams (no prioritization)
                    for stream in all_streams_data:
                        stream['_m3u_priority'] = 999  # Low priority for unspecified M3Us
                else:
                    # Create M3U ID to priority mapping (0 = highest priority)
                    m3u_priority_map = {m3u_id: idx for idx, m3u_id in enumerate(valid_m3u_ids)}
                    
                    # Filter streams by m3u_account (which is the M3U account ID) and add priority
                    filtered_streams = []
                    for s in all_streams_data:
                        m3u_id = s.get('m3u_account')
                        if m3u_id in valid_m3u_ids:
                            # Add priority metadata based on order in selected_m3us list
                            s['_m3u_priority'] = m3u_priority_map[m3u_id]
                            filtered_streams.append(s)
                    
                    logger.info(f"[Stream-Mapparr] Filtered streams from {len(all_streams_data)} to {len(filtered_streams)} based on M3U sources: {', '.join(selected_m3us)}")
                    logger.info(f"[Stream-Mapparr] M3U priority order: {', '.join([f'{name} (priority {idx})' for idx, name in enumerate(selected_m3us)])}")
                    all_streams_data = filtered_streams
                    m3u_filter_info = f" in M3U sources: {', '.join(selected_m3us)}"
            else:
                selected_m3us = []
                m3u_filter_info = " (all M3U sources)"
                # Add default priority to all streams (no prioritization when no M3U filter)
                for stream in all_streams_data:
                    stream['_m3u_priority'] = 999  # Low priority for unspecified M3Us

            self.loaded_channels = channels_to_process
            self.loaded_streams = all_streams_data

            processed_data = {
                "loaded_at": datetime.now().isoformat(),
                "profile_name": profile_names_str,
                "profile_names": profile_names,
                "profile_id": profile_id,
                "profile_ids": profile_ids,
                "selected_groups": selected_groups,
                "selected_stream_groups": selected_stream_groups,
                "selected_m3us": selected_m3us,
                "ignore_tags": ignore_tags,
                "visible_channel_limit": visible_channel_limit,
                "ignore_quality": ignore_quality,
                "ignore_regional": ignore_regional,
                "ignore_geographic": ignore_geographic,
                "ignore_misc": ignore_misc,
                "filter_dead_streams": settings.get('filter_dead_streams', PluginConfig.DEFAULT_FILTER_DEAD_STREAMS),
                "channels": channels_to_process,
                "streams": all_streams_data
            }

            self._send_progress_update("load_process_channels", 'running', 90, 'Saving processed data...', context)
            with open(self.processed_data_file, 'w') as f:
                json.dump(processed_data, f, indent=2)

            logger.info("[Stream-Mapparr] Channel and stream data loaded and saved successfully")
            
            # Send final completion notification
            success_msg = f"Successfully loaded {len(channels_to_process)} channels and {len(all_streams_data)} streams."
            self._send_progress_update("load_process_channels", 'success', 100, success_msg, context)
            
            return {"status": "success", "message": success_msg}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error loading channels: {str(e)}")
            return {"status": "error", "message": f"Error loading channels: {str(e)}"}

    def _generate_csv_header_comment(self, settings, processed_data, action_name="Unknown", is_scheduled=False, total_visible_channels=0, total_matched_streams=0, low_match_channels=None, threshold_data=None):
        """Generate CSV comment header with plugin version and settings info."""
        # Debug: Log all settings keys to see what's available
        LOGGER.debug(f"[Stream-Mapparr] CSV generation - All settings keys: {list(settings.keys())}")
        
        profile_name = processed_data.get('profile_name', 'N/A')
        selected_groups = processed_data.get('selected_groups', [])
        selected_stream_groups = processed_data.get('selected_stream_groups', [])
        selected_m3us = processed_data.get('selected_m3us', [])
        current_threshold = settings.get('fuzzy_match_threshold', PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD)

        # Build header with all settings except login credentials
        header_lines = [
            f"# Stream-Mapparr Export v{self.version}",
            f"# FuzzyMatcher Version: {fuzzy_matcher.__version__}",
            f"# Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "#",
            "# === Action Performed ===",
            f"# Action: {action_name}",
            f"# Execution Mode: {'Scheduled' if is_scheduled else 'Manual'}",
            f"# Dry Run Mode: {settings.get('dry_run_mode', False)}",
            "#",
            "# === Profile & Group Settings ===",
            f"# Profile Name(s): {profile_name}",
            f"# Selected Channel Groups: {', '.join(selected_groups) if selected_groups else '(all groups)'}",
            f"# Selected Stream Groups: {', '.join(selected_stream_groups) if selected_stream_groups else '(all stream groups)'}",
            f"# Selected M3U Sources: {', '.join(selected_m3us) if selected_m3us else '(all M3U sources)'}",
            "#",
            "# === Matching Settings ===",
            f"# Fuzzy Match Threshold: {current_threshold}",
            f"# Overwrite Streams: {settings.get('overwrite_streams', PluginConfig.DEFAULT_OVERWRITE_STREAMS)}",
            f"# Visible Channel Limit: {processed_data.get('visible_channel_limit', PluginConfig.DEFAULT_VISIBLE_CHANNEL_LIMIT)}",
            "#",
            "# === Tag Filter Settings ===",
            f"# Ignore Quality Tags: {processed_data.get('ignore_quality', True)}",
            f"# Ignore Regional Tags: {processed_data.get('ignore_regional', True)}",
            f"# Ignore Geographic Tags: {processed_data.get('ignore_geographic', True)}",
            f"# Ignore Misc Tags: {processed_data.get('ignore_misc', True)}",
            f"# Custom Ignore Tags: {', '.join(processed_data.get('ignore_tags', [])) if processed_data.get('ignore_tags') else '(none)'}",
            "#",
            "# === Database Settings ===",
        ]
        
        # Add enabled database settings
        # Note: Database settings are not passed in the settings dict, so we check the files directly
        enabled_dbs = []
        try:
            databases = self._get_channel_databases()
            for db_info in databases:
                db_id = db_info['id']
                setting_key = f"db_enabled_{db_id}"
                # Try to get from settings first, fallback to default
                is_enabled = settings.get(setting_key, db_info['default'])
                
                # Handle string boolean values
                if isinstance(is_enabled, str):
                    is_enabled = is_enabled.lower() in ('true', 'yes', '1', 'on')
                
                if is_enabled:
                    enabled_dbs.append(db_info['label'])
        except Exception as e:
            LOGGER.warning(f"[Stream-Mapparr] Could not determine enabled databases: {e}")
        
        header_lines.append(f"# Enabled Databases: {', '.join(enabled_dbs) if enabled_dbs else '(none)'}")
        
        header_lines.extend([
            "#",
            "# === IPTV Checker Integration ===",
            f"# Filter Dead Streams: {processed_data.get('filter_dead_streams', False)}",
            f"# Wait for IPTV Checker: {settings.get('wait_for_iptv_checker', PluginConfig.DEFAULT_WAIT_FOR_IPTV_CHECKER)}",
            f"# IPTV Checker Max Wait (hours): {settings.get('iptv_checker_max_wait_hours', PluginConfig.DEFAULT_IPTV_CHECKER_MAX_WAIT_HOURS)}",
            "#",
            "# === Scheduling Settings ===",
            f"# Timezone: {settings.get('timezone', PluginConfig.DEFAULT_TIMEZONE)}",
            f"# Scheduled Times: {settings.get('schedule_cron', '(none)')}",
            f"# Schedule: Sort Streams: {settings.get('scheduled_sort_streams', False)}",
            f"# Schedule: Match & Assign Streams: {settings.get('scheduled_match_streams', True)}",
            f"# Enable CSV Export: {settings.get('enable_scheduled_csv_export', PluginConfig.DEFAULT_ENABLE_CSV_EXPORT)}",
            "#",
            "# === API Settings ===",
            f"# Rate Limiting: {settings.get('rate_limiting', PluginConfig.DEFAULT_RATE_LIMITING)}",
            "#",
        ])
        
        # Analyze threshold data and token mismatches for recommendations
        recommendations_added = False
        
        if threshold_data:
            # Find lowest threshold with matches
            lowest_threshold_with_matches = None
            threshold_summary = {}
            token_mismatch_examples = []
            
            for channel_name, thresholds in threshold_data.items():
                for threshold, data in thresholds.items():
                    if isinstance(threshold, int) and data.get('streams'):
                        if lowest_threshold_with_matches is None or threshold < lowest_threshold_with_matches:
                            lowest_threshold_with_matches = threshold
                        
                        if threshold not in threshold_summary:
                            threshold_summary[threshold] = 0
                        threshold_summary[threshold] += len(data['streams'])
                        
                        # Analyze token mismatches
                        if threshold < current_threshold:
                            for stream in data['streams'][:2]:  # Check first 2 streams
                                mismatch_info = self._analyze_token_mismatch(channel_name, stream['name'])
                                if mismatch_info and len(token_mismatch_examples) < 3:
                                    token_mismatch_examples.append({
                                        'channel': channel_name,
                                        'stream': stream['name'],
                                        'mismatch': mismatch_info
                                    })
            
            # Add threshold recommendation if lower thresholds have matches
            if lowest_threshold_with_matches and lowest_threshold_with_matches < current_threshold:
                if not recommendations_added:
                    header_lines.append("# === RECOMMENDATIONS ===")
                    recommendations_added = True
                
                # Count additional streams at lower thresholds
                additional_at_lower = sum(count for thresh, count in threshold_summary.items() 
                                         if thresh < current_threshold)
                
                header_lines.extend([
                    f"# {additional_at_lower} additional stream(s) available at lower thresholds.",
                    f"# Consider lowering Fuzzy Match Threshold from {current_threshold} to {lowest_threshold_with_matches} for more results.",
                    "#"
                ])
            
            # Add token mismatch recommendations
            if token_mismatch_examples:
                if not recommendations_added:
                    header_lines.append("# === RECOMMENDATIONS ===")
                    recommendations_added = True
                
                # Collect unique mismatched tokens
                first_token_mismatches = set()
                last_token_mismatches = set()
                
                for example in token_mismatch_examples:
                    mismatch = example['mismatch']
                    if mismatch['position'] == 'first':
                        first_token_mismatches.update(mismatch['tokens'])
                    elif mismatch['position'] == 'last':
                        last_token_mismatches.update(mismatch['tokens'])
                
                if first_token_mismatches or last_token_mismatches:
                    header_lines.append("# Some channels have mismatched prefix/suffix tokens that prevent matching.")
                    header_lines.append("# Consider adding these to 'Custom Ignore Tags' setting:")
                    
                    if first_token_mismatches:
                        # Use comma-space separation for readability
                        tags_list = ', '.join(sorted(first_token_mismatches)[:5])
                        header_lines.append(f"#   Prefix tokens: {tags_list}")
                    
                    if last_token_mismatches:
                        # Use comma-space separation for readability
                        tags_list = ', '.join(sorted(last_token_mismatches)[:5])
                        header_lines.append(f"#   Suffix tokens: {tags_list}")
                    
                    header_lines.append("#")
                    header_lines.append("# Examples of mismatched channels:")
                    for example in token_mismatch_examples[:3]:
                        mismatch = example['mismatch']
                        tokens_str = ', '.join(mismatch['tokens'])
                        header_lines.append(f"#   {example['channel']} vs {example['stream']}")
                        header_lines.append(f"#     → Mismatched {mismatch['position']} token(s): {tokens_str}")
                    
                    header_lines.append("#")
        
        # Add low match channels recommendation (legacy from previous implementation)
        if low_match_channels and len(low_match_channels) > 0 and not recommendations_added:
            header_lines.extend([
                "# === RECOMMENDATIONS ===",
                f"# {len(low_match_channels)} channel(s) have 3 or fewer matches.",
                f"# Consider lowering Fuzzy Match Threshold for more results.",
                "#"
            ])
            
            # Show up to 5 examples
            examples_to_show = min(5, len(low_match_channels))
            header_lines.append(f"# Examples of channels with few matches:")
            for i, ch in enumerate(low_match_channels[:examples_to_show]):
                stream_list = ', '.join(ch['streams'][:3])
                header_lines.append(f"#   - {ch['name']} ({ch['count']} match{'es' if ch['count'] != 1 else ''}): {stream_list}")
            
            if len(low_match_channels) > 5:
                header_lines.append(f"#   ... and {len(low_match_channels) - 5} more channel(s)")
            
            header_lines.append("#")
        
        return '\n'.join(header_lines) + '\n'

    def _analyze_token_mismatch(self, channel_name, stream_name):
        """Analyze if channel and stream names have mismatched first or last tokens.
        
        Returns dict with mismatch info or None if tokens match well.
        """
        # Clean names for comparison
        channel_clean = re.sub(r'[^\w\s]', ' ', channel_name.lower())
        stream_clean = re.sub(r'[^\w\s]', ' ', stream_name.lower())
        
        channel_tokens = [t for t in channel_clean.split() if len(t) > 1]
        stream_tokens = [t for t in stream_clean.split() if len(t) > 1]
        
        if not channel_tokens or not stream_tokens:
            return None
        
        mismatched_tokens = []
        position = None
        
        # Check first token mismatch
        if len(channel_tokens) >= 2 and len(stream_tokens) >= 2:
            if channel_tokens[0] != stream_tokens[0]:
                # Check if second tokens match (indicating first token is the issue)
                if channel_tokens[1] == stream_tokens[1] or (len(channel_tokens) >= 3 and len(stream_tokens) >= 3 and channel_tokens[1] == stream_tokens[1]):
                    # First token mismatch
                    if channel_tokens[0] not in stream_tokens:
                        mismatched_tokens.append(channel_tokens[0])
                    if stream_tokens[0] not in channel_tokens:
                        mismatched_tokens.append(stream_tokens[0])
                    position = 'first'
        
        # Check last token mismatch
        if len(channel_tokens) >= 2 and len(stream_tokens) >= 2:
            if channel_tokens[-1] != stream_tokens[-1]:
                # Check if second-to-last tokens match (indicating last token is the issue)
                if channel_tokens[-2] == stream_tokens[-2] or (len(channel_tokens) >= 3 and len(stream_tokens) >= 3 and channel_tokens[-2] == stream_tokens[-2]):
                    # Last token mismatch
                    if channel_tokens[-1] not in stream_tokens:
                        if channel_tokens[-1] not in mismatched_tokens:
                            mismatched_tokens.append(channel_tokens[-1])
                    if stream_tokens[-1] not in channel_tokens:
                        if stream_tokens[-1] not in mismatched_tokens:
                            mismatched_tokens.append(stream_tokens[-1])
                    if not position:  # Don't override first token position
                        position = 'last'
        
        if mismatched_tokens and position:
            return {
                'tokens': mismatched_tokens,
                'position': position
            }
        
        return None

    def _sort_channels_by_priority(self, channels):
        """Sort channels by quality tag priority, then by channel number."""
        def get_priority_key(channel):
            quality_tag = self._extract_channel_quality_tag(channel['name'])
            try:
                quality_index = self.CHANNEL_QUALITY_TAG_ORDER.index(quality_tag)
            except ValueError:
                quality_index = len(self.CHANNEL_QUALITY_TAG_ORDER)

            channel_number = channel.get('channel_number', 999999)
            if channel_number is None: channel_number = 999999
            return (quality_index, channel_number)

        return sorted(channels, key=get_priority_key)

    def preview_changes_action(self, settings, logger, context=None):
        """Preview which streams will be added to channels without making changes."""
        # Always reload channels to ensure fresh data
        logger.info("[Stream-Mapparr] Loading fresh channel and stream data...")
        self._send_progress_update("preview_changes", 'running', 0, 'Loading channels and streams...', context)
        load_result = self.load_process_channels_action(settings, logger, context)
        if load_result.get('status') != 'success':
            return load_result

        try:
            self._send_progress_update("preview_changes", 'running', 5, 'Initializing preview...', context)
            limiter = SmartRateLimiter(settings.get("rate_limiting", "none"), logger)

            self._send_progress_update("preview_changes", 'running', 10, 'Validating settings...', context)
            has_errors, validation_results, token = self._validate_plugin_settings(settings, logger)
            if has_errors: return {"status": "error", "message": "Validation failed."}

            channels_data = self._load_channels_data(logger, settings)
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)

            channels = processed_data.get('channels', [])
            streams = processed_data.get('streams', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', 1)
            ignore_tags = processed_data.get('ignore_tags', [])

            ignore_quality = processed_data.get('ignore_quality', True)
            ignore_regional = processed_data.get('ignore_regional', True)
            ignore_geographic = processed_data.get('ignore_geographic', True)
            ignore_misc = processed_data.get('ignore_misc', True)
            filter_dead = processed_data.get('filter_dead_streams', PluginConfig.DEFAULT_FILTER_DEAD_STREAMS)

            channel_groups = {}
            for channel in channels:
                channel_info = self._get_channel_info_from_json(channel['name'], channels_data, logger)
                if self._is_ota_channel(channel_info):
                    callsign = channel_info.get('callsign', '')
                    group_key = f"OTA_{callsign}" if callsign else self._clean_channel_name(channel['name'], ignore_tags)
                else:
                    group_key = self._clean_channel_name(channel['name'], ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc)

                if group_key not in channel_groups: channel_groups[group_key] = []
                channel_groups[group_key].append(channel)

            all_matches = []
            total_channels_to_update = 0
            low_match_channels = []  # Track channels with few matches for recommendations
            threshold_data = {}  # Track threshold analysis for recommendations
            current_threshold = settings.get('fuzzy_match_threshold', PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD)
            try:
                current_threshold = int(current_threshold)
            except (ValueError, TypeError):
                current_threshold = 85

            self._send_progress_update("preview_changes", 'running', 30, f'Analyzing {len(channel_groups)} channel groups...', context)

            # Initialize progress tracker for channel group processing
            progress_tracker = ProgressTracker(
                total_items=len(channel_groups),
                action_id="preview_changes",
                logger=logger,
                context=context,
                send_progress_callback=self._send_progress_update
            )
            progress_tracker.set_progress_range(30, 80)  # This phase handles 30-80% of progress
            
            processed_groups = 0
            total_groups = len(channel_groups)
            group_stats = {}  # Track stats for each group

            for group_key, group_channels in channel_groups.items():
                limiter.wait()
                
                # Update progress tracker (automatically sends updates every minute)
                progress_tracker.update(items_processed=1)
                
                sorted_channels = self._sort_channels_by_priority(group_channels)
                
                # Get matches at current threshold for the primary channel
                matched_streams, cleaned_channel_name, cleaned_stream_names, match_reason, database_used = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc, channels_data, filter_dead
                )

                # Track group stats
                group_stats[group_key] = {
                    'channel_count': len(group_channels),
                    'stream_count': len(matched_streams)
                }

                channels_to_update = sorted_channels[:visible_channel_limit]
                channels_not_updated = sorted_channels[visible_channel_limit:]

                for channel in channels_to_update:
                    match_count = len(matched_streams)
                    
                    # Get detailed threshold analysis
                    threshold_matches = self._get_matches_at_thresholds(
                        channel, streams, logger, ignore_tags, ignore_quality,
                        ignore_regional, ignore_geographic, ignore_misc, channels_data,
                        current_threshold
                    )
                    
                    # Store threshold analysis for recommendations
                    threshold_data[channel['name']] = threshold_matches
                    
                    # Add row for current threshold matches
                    all_matches.append({
                        "channel_id": channel['id'],
                        "channel_name": channel['name'],
                        "threshold": current_threshold,
                        "matched_streams": match_count,
                        "stream_names": [s['name'] for s in matched_streams],
                        "will_update": True,
                        "is_current": True
                    })
                    total_channels_to_update += 1
                    
                    # Check if channel might benefit from lower threshold
                    if match_count > 0 and match_count <= 3:
                        low_match_channels.append({
                            'name': channel['name'],
                            'count': match_count,
                            'streams': [s['name'] for s in matched_streams[:3]]
                        })
                    
                    # Add rows for additional matches at lower thresholds
                    for threshold in sorted([t for t in threshold_matches.keys() if isinstance(t, int) and t < current_threshold], reverse=True):
                        threshold_info = threshold_matches[threshold]
                        threshold_streams = threshold_info['streams']
                        
                        # Find streams that are NEW at this threshold (not in current matches)
                        current_stream_ids = {s['id'] for s in matched_streams}
                        new_streams = [s for s in threshold_streams if s['id'] not in current_stream_ids]
                        
                        if new_streams:
                            all_matches.append({
                                "channel_id": channel['id'],
                                "channel_name": f"  └─ (at threshold {threshold})",
                                "threshold": threshold,
                                "matched_streams": len(new_streams),
                                "stream_names": [s['name'] for s in new_streams],
                                "will_update": False,
                                "is_current": False
                            })

                for channel in channels_not_updated:
                    all_matches.append({
                        "channel_id": channel['id'],
                        "channel_name": channel['name'],
                        "threshold": current_threshold,
                        "matched_streams": len(matched_streams),
                        "stream_names": [],
                        "will_update": False,
                        "is_current": True
                    })

            # Log channel group statistics
            logger.info(f"[Stream-Mapparr] Processed {len(channel_groups)} channel groups with {len(channels)} total channels")
            for group_key, stats in list(group_stats.items())[:10]:  # Log first 10 groups
                logger.info(f"[Stream-Mapparr]   - Group '{group_key}': {stats['channel_count']} channel(s), {stats['stream_count']} matched stream(s)")
            if len(channel_groups) > 10:
                logger.info(f"[Stream-Mapparr]   ... and {len(channel_groups) - 10} more groups")

            self._send_progress_update("preview_changes", 'running', 85, 'Generating CSV report...', context)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"stream_mapparr_preview_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            os.makedirs("/data/exports", exist_ok=True)

            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                header_comment = self._generate_csv_header_comment(settings, processed_data,
                                                                   action_name="Preview Changes (Dry Run)",
                                                                   is_scheduled=False,
                                                                   low_match_channels=low_match_channels,
                                                                   threshold_data=threshold_data)
                csvfile.write(header_comment)
                fieldnames = ['will_update', 'threshold', 'channel_id', 'channel_name', 'matched_streams', 'stream_names']
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for match in all_matches:
                    writer.writerow({
                        'will_update': 'Yes' if match['will_update'] else 'No',
                        'threshold': match.get('threshold', current_threshold),
                        'channel_id': match['channel_id'],
                        'channel_name': match['channel_name'],
                        'matched_streams': match['matched_streams'],
                        'stream_names': '; '.join(match.get('stream_names', []))
                    })

            # Log CSV creation prominently
            logger.info(f"[Stream-Mapparr] 📄 CSV PREVIEW REPORT CREATED: {filepath}")
            logger.info(f"[Stream-Mapparr] Preview shows {total_channels_to_update} channels will be updated")

            message = f"Preview complete. {total_channels_to_update} channels will be updated. Report saved to {filepath}"
            self._send_progress_update("preview_changes", 'success', 100, message, context)
            return {"status": "success", "message": message}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error previewing changes: {str(e)}")
            return {"status": "error", "message": f"Error previewing changes: {str(e)}"}

    def add_streams_to_channels_action(self, settings, logger, is_scheduled=False, context=None):
        """Add matching streams to channels and replace existing stream assignments."""
        # Check dry run mode
        dry_run = settings.get('dry_run_mode', False)
        if isinstance(dry_run, str):
            dry_run = dry_run.lower() in ('true', 'yes', '1')
        
        mode_label = "DRY RUN (Preview)" if dry_run else "LIVE MODE"
        logger.info(f"[Stream-Mapparr] === MATCH & ASSIGN STREAMS ACTION STARTED ({mode_label}) ===")
        
        # Always reload channels to ensure fresh data
        logger.info("[Stream-Mapparr] Loading fresh channel and stream data...")
        self._send_progress_update("add_streams_to_channels", 'running', 0, 'Loading channels and streams...', context)
        load_result = self.load_process_channels_action(settings, logger, context)
        if load_result.get('status') != 'success':
            return load_result

        try:
            self._send_progress_update("add_streams_to_channels", 'running', 5, 'Initializing stream assignment...', context)
            limiter = SmartRateLimiter(settings.get("rate_limiting", "none"), logger)
            
            self._send_progress_update("add_streams_to_channels", 'running', 10, 'Authenticating...', context)
            token, error = self._get_api_token(settings, logger)
            if error: return {"status": "error", "message": error}

            channels_data = self._load_channels_data(logger, settings)
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)

            channels = processed_data.get('channels', [])
            streams = processed_data.get('streams', [])
            ignore_tags = processed_data.get('ignore_tags', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', PluginConfig.DEFAULT_VISIBLE_CHANNEL_LIMIT)
            overwrite_streams = settings.get('overwrite_streams', PluginConfig.DEFAULT_OVERWRITE_STREAMS)
            if isinstance(overwrite_streams, str): overwrite_streams = overwrite_streams.lower() in ('true', 'yes', '1')

            ignore_quality = processed_data.get('ignore_quality', True)
            ignore_regional = processed_data.get('ignore_regional', True)
            ignore_geographic = processed_data.get('ignore_geographic', True)
            ignore_misc = processed_data.get('ignore_misc', True)
            filter_dead = processed_data.get('filter_dead_streams', PluginConfig.DEFAULT_FILTER_DEAD_STREAMS)

            channel_groups = {}
            for channel in channels:
                channel_info = self._get_channel_info_from_json(channel['name'], channels_data, logger)
                if self._is_ota_channel(channel_info):
                    callsign = channel_info.get('callsign', '')
                    group_key = f"OTA_{callsign}" if callsign else self._clean_channel_name(channel['name'], ignore_tags)
                else:
                    group_key = self._clean_channel_name(channel['name'], ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc)

                if group_key not in channel_groups: channel_groups[group_key] = []
                channel_groups[group_key].append(channel)

            channels_updated = 0
            total_streams_added = 0
            channels_skipped = 0

            self._send_progress_update("add_streams_to_channels", 'running', 20,
                                      f'Processing {len(channel_groups)} channel groups...', context)

            # Initialize progress tracker for channel group processing
            progress_tracker = ProgressTracker(
                total_items=len(channel_groups),
                action_id="add_streams_to_channels",
                logger=logger,
                context=context,
                send_progress_callback=self._send_progress_update
            )
            progress_tracker.set_progress_range(20, 80)  # This phase handles 20-80% of progress

            processed_groups = 0
            total_groups = len(channel_groups)
            group_stats = {}  # Track stats for each group

            for group_key, group_channels in channel_groups.items():
                limiter.wait() # Rate limit processing
                sorted_channels = self._sort_channels_by_priority(group_channels)
                matched_streams, _, _, _, _ = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc, channels_data, filter_dead
                )

                # Track group stats
                group_stats[group_key] = {
                    'channel_count': len(group_channels),
                    'stream_count': len(matched_streams)
                }

                channels_to_update = sorted_channels[:visible_channel_limit]

                for channel in channels_to_update:
                    channel_id = channel['id']

                    # Validate that channel exists in database before attempting operations
                    if not Channel.objects.filter(id=channel_id).exists():
                        logger.warning(f"[Stream-Mapparr] Skipping channel '{channel['name']}' (ID: {channel_id}) - channel no longer exists in database. Consider reloading channels.")
                        channels_skipped += 1
                        continue

                    try:
                        if matched_streams:
                            # Only apply changes if not in dry run mode
                            if not dry_run:
                                if overwrite_streams:
                                    ChannelStream.objects.filter(channel_id=channel_id).delete()

                                existing_stream_ids = set(ChannelStream.objects.filter(channel_id=channel_id).values_list('stream_id', flat=True))
                                streams_added = 0
                                for index, stream in enumerate(matched_streams):
                                    if not overwrite_streams and stream['id'] in existing_stream_ids: continue
                                    # Set order field to maintain quality-based sorting (4K -> UHD -> FHD -> HD -> SD)
                                    # matched_streams is already sorted by _sort_streams_by_quality in _match_streams_to_channel
                                    ChannelStream.objects.create(channel_id=channel_id, stream_id=stream['id'], order=index)
                                    streams_added += 1
                                
                                total_streams_added += streams_added
                            else:
                                # Dry run: just count what would be added
                                streams_added = len(matched_streams)
                                total_streams_added += streams_added

                            channels_updated += 1
                        else:
                            if not dry_run and overwrite_streams:
                                ChannelStream.objects.filter(channel_id=channel_id).delete()
                    except Exception as e:
                        logger.error(f"[Stream-Mapparr] Failed to update channel '{channel['name']}': {e}")
                
                # Update progress tracker (automatically sends updates every minute with ETA)
                progress_tracker.update(items_processed=1)

            # Log channel group statistics
            logger.info(f"[Stream-Mapparr] Processed {len(channel_groups)} channel groups with {len(channels)} total channels")
            for group_key, stats in list(group_stats.items())[:10]:  # Log first 10 groups
                logger.info(f"[Stream-Mapparr]   - Group '{group_key}': {stats['channel_count']} channel(s), {stats['stream_count']} matched stream(s)")
            if len(channel_groups) > 10:
                logger.info(f"[Stream-Mapparr]   ... and {len(channel_groups) - 10} more groups")

            # CSV Export - create if dry run OR if setting is enabled
            create_csv = settings.get('enable_scheduled_csv_export', PluginConfig.DEFAULT_ENABLE_CSV_EXPORT)
            if isinstance(create_csv, str):
                create_csv = create_csv.lower() in ('true', 'yes', '1')
            
            # Always create CSV in dry run mode
            if dry_run or create_csv:
                self._send_progress_update("add_streams_to_channels", 'running', 85, 'Generating CSV export...', context)
                try:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"stream_mapparr_{timestamp}.csv"
                    filepath = os.path.join("/data/exports", filename)
                    os.makedirs("/data/exports", exist_ok=True)

                    # Collect all channel-stream mappings for CSV with lower threshold recommendations
                    csv_data = []
                    low_match_channels = []  # Track channels with few matches for recommendations
                    threshold_data = {}  # Track threshold analysis for recommendations
                    current_threshold = settings.get('fuzzy_match_threshold', PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD)
                    try:
                        current_threshold = int(current_threshold)
                    except (ValueError, TypeError):
                        current_threshold = 85
                    
                    for group_key, group_channels in channel_groups.items():
                        sorted_channels = self._sort_channels_by_priority(group_channels)
                        matched_streams, _, _, _, _ = self._match_streams_to_channel(
                            sorted_channels[0], streams, logger, ignore_tags, ignore_quality,
                            ignore_regional, ignore_geographic, ignore_misc, channels_data
                        )

                        channels_to_update = sorted_channels[:visible_channel_limit]
                        for channel in channels_to_update:
                            match_count = len(matched_streams)
                            
                            # Get detailed threshold analysis
                            threshold_matches = self._get_matches_at_thresholds(
                                channel, streams, logger, ignore_tags, ignore_quality,
                                ignore_regional, ignore_geographic, ignore_misc, channels_data,
                                current_threshold
                            )
                            
                            # Store threshold analysis for recommendations
                            threshold_data[channel['name']] = threshold_matches
                            
                            # Add row for current threshold matches
                            csv_data.append({
                                'channel_id': channel['id'],
                                'channel_name': channel['name'],
                                'threshold': current_threshold,
                                'matched_streams': match_count,
                                'stream_names': '; '.join([s['name'] for s in matched_streams])
                            })
                            
                            # Check if channel might benefit from lower threshold
                            if match_count > 0 and match_count <= 3:
                                low_match_channels.append({
                                    'name': channel['name'],
                                    'count': match_count,
                                    'streams': [s['name'] for s in matched_streams[:3]]
                                })
                            
                            # Add rows for additional matches at lower thresholds
                            for threshold in sorted([t for t in threshold_matches.keys() if isinstance(t, int) and t < current_threshold], reverse=True):
                                threshold_info = threshold_matches[threshold]
                                threshold_streams = threshold_info['streams']
                                
                                # Find streams that are NEW at this threshold (not in current matches)
                                current_stream_ids = {s['id'] for s in matched_streams}
                                new_streams = [s for s in threshold_streams if s['id'] not in current_stream_ids]
                                
                                if new_streams:
                                    csv_data.append({
                                        'channel_id': channel['id'],
                                        'channel_name': f"  └─ (at threshold {threshold})",
                                        'threshold': threshold,
                                        'matched_streams': len(new_streams),
                                        'stream_names': '; '.join([s['name'] for s in new_streams])
                                    })

                    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                        header_comment = self._generate_csv_header_comment(settings, processed_data,
                                                                          action_name="Match & Assign Streams",
                                                                          is_scheduled=is_scheduled,
                                                                          total_visible_channels=channels_updated,
                                                                          total_matched_streams=total_streams_added,
                                                                          low_match_channels=low_match_channels,
                                                                          threshold_data=threshold_data)
                        csvfile.write(header_comment)
                        fieldnames = ['threshold', 'channel_id', 'channel_name', 'matched_streams', 'stream_names']
                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writeheader()
                        for row in csv_data:
                            writer.writerow(row)

                    # Log CSV creation prominently
                    logger.info(f"[Stream-Mapparr] 📄 CSV EXPORT CREATED: {filepath}")
                    logger.info(f"[Stream-Mapparr] Export contains {len(csv_data)} channel updates")
                    csv_created = filepath
                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Failed to create CSV export: {e}")
                    csv_created = None
            else:
                csv_created = None

            # Only trigger frontend refresh if not dry run
            if not dry_run:
                self._trigger_frontend_refresh(settings, logger)

            # Send final completion notification with CSV info
            if dry_run:
                success_msg = f"✅ PREVIEW COMPLETED (DRY RUN)\n\nWould update {channels_updated} channels with {total_streams_added} streams."
            else:
                success_msg = f"✅ MATCH & ASSIGN COMPLETED\n\nUpdated {channels_updated} channels with {total_streams_added} streams."
            
            if csv_created:
                success_msg += f"\n📄 Report: {csv_created}"
            if channels_skipped > 0:
                success_msg += f"\n⚠️ Skipped {channels_skipped} deleted channel(s)."
            
            logger.info(f"[Stream-Mapparr] {success_msg}")
            self._send_progress_update("add_streams_to_channels", 'success', 100, success_msg, context)

            return {"status": "success", "message": success_msg}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error adding streams: {str(e)}")
            return {"status": "error", "message": f"Error adding streams: {str(e)}"}

    def match_us_ota_only_action(self, settings, logger, context=None):
        """Match and assign streams to US OTA channels using callsign matching only.
        
        This action:
        1. Builds US callsign database from US_channels.json
        2. Loads channels from Dispatcharr (filtered by profile/groups)
        3. For each channel, extracts callsign and matches against US database
        4. Searches streams for uppercase callsign occurrences only
        5. Assigns matched streams (or previews if dry run enabled)
        """
        try:
            # Check dry run mode
            dry_run = settings.get('dry_run_mode', False)
            if isinstance(dry_run, str):
                dry_run = dry_run.lower() in ('true', 'yes', '1')
            
            mode_label = "PREVIEW" if dry_run else "LIVE"
            logger.info(f"[Stream-Mapparr] ========== US OTA MATCHING STARTED ({mode_label} MODE) ==========")
            
            # Build US callsign database
            logger.info("[Stream-Mapparr] Building US callsign database from US_channels.json...")
            us_callsign_db = self._build_us_callsign_database(logger)
            
            if not us_callsign_db:
                error_msg = "Failed to build US callsign database. Check that US_channels.json exists and is valid."
                logger.error(f"[Stream-Mapparr] {error_msg}")
                return {"status": "error", "message": error_msg}
            
            logger.info(f"[Stream-Mapparr] Loaded {len(us_callsign_db)} unique US callsigns")
            
            # Validate settings
            validation_result = self.validate_settings_action(settings, logger)
            if validation_result.get('status') != 'success':
                return validation_result
            
            # Get API token
            logger.info("[Stream-Mapparr] Authenticating with Dispatcharr API...")
            token, error = self.get_or_refresh_api_token(settings, logger)
            if error or not token:
                return {"status": "error", "message": f"Failed to authenticate with Dispatcharr API: {error}"}
            
            # Get rate limiter
            rate_limiting = settings.get("rate_limiting", PluginConfig.DEFAULT_RATE_LIMITING)
            limiter = SmartRateLimiter(rate_limiting, logger)
            
            # Load channels (same logic as load_process_channels_action for profile/group filtering)
            logger.info("[Stream-Mapparr] Loading channels from Dispatcharr...")
            
            profile_names_str = settings.get("profile_name") or ""
            profile_names_str = profile_names_str.strip() if profile_names_str else ""
            selected_groups_str = settings.get("selected_groups") or ""
            selected_groups_str = selected_groups_str.strip() if selected_groups_str else ""
            
            profile_names = [name.strip() for name in profile_names_str.split(',') if name.strip()]
            
            # Fetch profiles
            profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger, limiter=limiter)
            
            target_profiles = []
            profile_ids = []
            for profile_name in profile_names:
                found_profile = None
                for profile in profiles:
                    if profile.get('name', '').lower() == profile_name.lower():
                        found_profile = profile
                        break
                if not found_profile:
                    return {"status": "error", "message": f"Profile '{profile_name}' not found."}
                target_profiles.append(found_profile)
                profile_ids.append(found_profile['id'])
            
            # Fetch groups if group filtering is specified
            if selected_groups_str:
                all_groups = []
                page = 1
                while True:
                    try:
                        api_groups = self._get_api_data(f"/api/channels/groups/?page={page}", token, settings, logger, limiter=limiter)
                    except Exception as e:
                        if page > 1:
                            break
                        else:
                            raise
                    
                    if isinstance(api_groups, dict) and 'results' in api_groups:
                        results = api_groups['results']
                        if not results:
                            break
                        all_groups.extend(results)
                        if not api_groups.get('next'):
                            break
                        page += 1
                    elif isinstance(api_groups, list):
                        if not api_groups:
                            break
                        all_groups.extend(api_groups)
                        break
                    else:
                        break
                
                group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            
            # Fetch all channels
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger, limiter=limiter)
            
            # Filter channels by profile (enabled=True in profile)
            channels_in_profile = []
            for channel in all_channels:
                channel_id = channel['id']
                is_in_profile = ChannelProfileMembership.objects.filter(
                    channel_id=channel_id,
                    channel_profile_id__in=profile_ids,
                    enabled=True
                ).exists()
                if is_in_profile:
                    channels_in_profile.append(channel)
            
            # Filter by groups if specified
            if selected_groups_str:
                selected_groups = [g.strip() for g in selected_groups_str.split(',') if g.strip()]
                valid_group_ids = [group_name_to_id[name] for name in selected_groups if name in group_name_to_id]
                if not valid_group_ids:
                    return {"status": "error", "message": "None of the specified groups were found."}
                
                filtered_channels = [ch for ch in channels_in_profile if ch.get('channel_group_id') in valid_group_ids]
                channels = filtered_channels
                logger.info(f"[Stream-Mapparr] Filtered to {len(channels)} channels in groups: {', '.join(selected_groups)}")
            else:
                channels = channels_in_profile
                logger.info(f"[Stream-Mapparr] Using all channels from profile (no group filter)")
            
            if not channels:
                error_msg = "No channels found. Check profile and group filters."
                logger.error(f"[Stream-Mapparr] {error_msg}")
                return {"status": "error", "message": error_msg}
            
            logger.info(f"[Stream-Mapparr] Loaded {len(channels)} channels")
            
            # Load all streams
            logger.info("[Stream-Mapparr] Loading all streams...")
            all_streams = []
            page = 1
            while True:
                endpoint = f"/api/channels/streams/?page={page}&page_size=100"

                try:
                    streams_response = self._get_api_data(endpoint, token, settings, logger, limiter=limiter)
                except Exception as e:
                    # If we get an error (e.g., 404 for non-existent page), we've reached the end
                    if page > 1:
                        logger.debug(f"[Stream-Mapparr] No more pages available (attempted page {page})")
                        break
                    else:
                        # If error on first page, re-raise
                        raise

                # Handle both paginated and non-paginated responses
                if isinstance(streams_response, dict) and 'results' in streams_response:
                    results = streams_response['results']

                    # Check if we got empty results
                    if not results:
                        logger.debug("[Stream-Mapparr] Reached last page of streams (empty results)")
                        break

                    all_streams.extend(results)
                    logger.debug(f"[Stream-Mapparr] Fetched page {page}: {len(results)} streams (total so far: {len(all_streams)})")

                    # Stop if this page had fewer results than page_size (last page)
                    if len(results) < 100:
                        logger.debug("[Stream-Mapparr] Reached last page of streams")
                        break

                    page += 1
                elif isinstance(streams_response, list):
                    # Check if we got empty results
                    if not streams_response:
                        logger.debug("[Stream-Mapparr] Reached last page of streams (empty results)")
                        break

                    # List response - could still be paginated
                    all_streams.extend(streams_response)
                    logger.debug(f"[Stream-Mapparr] Fetched page {page}: {len(streams_response)} streams (total so far: {len(all_streams)})")

                    # If we got exactly 100 results, there might be more pages
                    if len(streams_response) == 100:
                        page += 1
                    else:
                        logger.debug("[Stream-Mapparr] Reached last page of streams")
                        break
                else:
                    logger.warning("[Stream-Mapparr] Unexpected streams response format")
                    break
            
            if not all_streams:
                error_msg = "No streams found in Dispatcharr"
                logger.error(f"[Stream-Mapparr] {error_msg}")
                return {"status": "error", "message": error_msg}
            
            logger.info(f"[Stream-Mapparr] Loaded {len(all_streams)} streams")
            
            # Filter dead streams if enabled
            filter_dead = settings.get('filter_dead_streams', False)
            if isinstance(filter_dead, str):
                filter_dead = filter_dead.lower() in ('true', 'yes', '1')
            
            working_streams = all_streams
            if filter_dead:
                logger.info("[Stream-Mapparr] Filtering dead streams (0x0 resolution)...")
                working_streams = self._filter_working_streams(all_streams, logger)
                logger.info(f"[Stream-Mapparr] {len(working_streams)} working streams (filtered out {len(all_streams) - len(working_streams)} dead)")
            
            # Initialize fuzzy matcher for callsign extraction
            if not self.fuzzy_matcher:
                match_threshold = settings.get("fuzzy_match_threshold", PluginConfig.DEFAULT_FUZZY_MATCH_THRESHOLD)
                if isinstance(match_threshold, str):
                    match_threshold = int(match_threshold)
                self._initialize_fuzzy_matcher(match_threshold)
            
            # Match channels using US OTA callsign database
            logger.info("[Stream-Mapparr] Matching channels using US OTA callsign database...")
            logger.info("[Stream-Mapparr] Note: Only channels with valid US callsigns will be matched")
            
            matched_channels = []
            skipped_no_callsign = 0
            skipped_not_in_db = 0
            skipped_no_streams = 0
            
            for idx, channel in enumerate(channels, 1):
                channel_name = channel.get('name', '')
                channel_id = channel.get('id')
                
                if idx % 100 == 0:
                    logger.info(f"[Stream-Mapparr] Processing channel {idx}/{len(channels)}...")
                
                # Extract callsign from Dispatcharr channel name using fuzzy_matcher
                callsign = self.fuzzy_matcher.extract_callsign(channel_name)
                
                if not callsign:
                    logger.debug(f"[Stream-Mapparr] Skipping '{channel_name}' - no US callsign found")
                    skipped_no_callsign += 1
                    continue
                
                # Normalize to base callsign (remove suffixes like -TV, -DT)
                base_callsign = self.fuzzy_matcher.normalize_callsign(callsign)
                
                # Check if callsign exists in US database
                if base_callsign not in us_callsign_db:
                    logger.debug(f"[Stream-Mapparr] Skipping '{channel_name}' - callsign '{base_callsign}' not in US database")
                    skipped_not_in_db += 1
                    continue
                
                # Search streams for callsign (uppercase only)
                matching_streams = []
                
                # Create regex pattern for base callsign + variations
                # Matches: WKRG, WKRG-DT, WKRG-DT2, etc. (case-sensitive)
                callsign_pattern = r'\b' + re.escape(base_callsign) + r'(?:-[A-Z]{2}\d?)?\b'
                
                for stream in working_streams:
                    stream_name = stream.get('name', '')
                    
                    # Search for uppercase callsign occurrences only
                    if re.search(callsign_pattern, stream_name):
                        matching_streams.append(stream)
                
                if not matching_streams:
                    logger.debug(f"[Stream-Mapparr] No streams found for '{channel_name}' (callsign: {base_callsign})")
                    skipped_no_streams += 1
                    continue
                
                # Sort streams by quality
                sorted_streams = self._sort_streams_by_quality(matching_streams)
                sorted_streams = self._deduplicate_streams(sorted_streams)
                
                # Add to matched channels list
                matched_channels.append({
                    'channel_id': channel_id,
                    'channel_name': channel_name,
                    'callsign': base_callsign,
                    'stream_ids': [s['id'] for s in sorted_streams],
                    'stream_names': [s['name'] for s in sorted_streams],
                    'match_type': f'US OTA callsign: {base_callsign}'
                })
                
                logger.debug(f"[Stream-Mapparr] Matched '{channel_name}' ({base_callsign}) with {len(sorted_streams)} stream(s)")
            
            # Log summary
            logger.info(f"[Stream-Mapparr] ===== US OTA Matching Summary =====")
            logger.info(f"[Stream-Mapparr] Total channels processed: {len(channels)}")
            logger.info(f"[Stream-Mapparr] Channels matched: {len(matched_channels)}")
            logger.info(f"[Stream-Mapparr] Skipped (no callsign): {skipped_no_callsign}")
            logger.info(f"[Stream-Mapparr] Skipped (not in US database): {skipped_not_in_db}")
            logger.info(f"[Stream-Mapparr] Skipped (no matching streams): {skipped_no_streams}")
            
            if not matched_channels:
                error_msg = "No channels matched. All channels were skipped (no callsigns or not in US database)."
                logger.warning(f"[Stream-Mapparr] {error_msg}")
                return {"status": "error", "message": error_msg}
            
            # Generate CSV export
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"stream_mapparr_us_ota_{timestamp}.csv"
            csv_filepath = os.path.join(PluginConfig.EXPORTS_DIR, csv_filename)
            
            logger.info(f"[Stream-Mapparr] Generating CSV export: {csv_filename}")
            
            try:
                os.makedirs(PluginConfig.EXPORTS_DIR, exist_ok=True)
                
                # Create processed_data for CSV header
                processed_data = {
                    'profile_name': profile_names_str,
                    'selected_groups': [selected_groups_str] if selected_groups_str else [],
                    'selected_stream_groups': [],
                    'selected_m3us': []
                }
                
                with open(csv_filepath, 'w', newline='', encoding='utf-8') as csvfile:
                    # Write header comment
                    csvfile.write(self._generate_csv_header_comment(
                        settings=settings,
                        processed_data=processed_data,
                        action_name="Match US OTA Only",
                        is_scheduled=False
                    ))
                    
                    # Write CSV data
                    writer = csv.writer(csvfile)
                    writer.writerow(['will_update', 'channel_id', 'channel_name', 'callsign', 'matched_streams', 'stream_names'])
                    
                    for channel_data in matched_channels:
                        will_update = "yes" if not dry_run else "preview"
                        stream_names = "; ".join(channel_data['stream_names'])
                        
                        writer.writerow([
                            will_update,
                            channel_data['channel_id'],
                            channel_data['channel_name'],
                            channel_data['callsign'],
                            len(channel_data['stream_ids']),
                            stream_names
                        ])
                
                logger.info(f"📄 [Stream-Mapparr] CSV export created: {csv_filepath}")
                
            except Exception as csv_error:
                logger.error(f"[Stream-Mapparr] Error creating CSV: {str(csv_error)}")
            
            # If dry run, stop here
            if dry_run:
                logger.info(f"✅ [Stream-Mapparr] US OTA MATCHING PREVIEW COMPLETED")
                logger.info(f"[Stream-Mapparr] Matched {len(matched_channels)} channels")
                logger.info(f"[Stream-Mapparr] CSV preview: {csv_filepath}")
                
                return {
                    "status": "success",
                    "message": f"✅ US OTA Matching Preview Complete\n\n"
                               f"📊 Matched {len(matched_channels)} channels\n"
                               f"📄 CSV: {csv_filename}\n\n"
                               f"💡 Disable 'Dry Run Mode' to actually assign streams."
                }
            
            # Assign streams to channels (LIVE MODE using Django ORM)
            overwrite = settings.get('overwrite_streams', PluginConfig.DEFAULT_OVERWRITE_STREAMS)
            if isinstance(overwrite, str):
                overwrite = overwrite.lower() in ('true', 'yes', '1')
            
            success_count = 0
            error_count = 0
            
            for idx, channel_data in enumerate(matched_channels, 1):
                try:
                    channel_id = channel_data['channel_id']
                    stream_ids = channel_data['stream_ids']
                    
                    if idx % 50 == 0:
                        logger.info(f"[Stream-Mapparr] Assigning streams {idx}/{len(matched_channels)}...")
                    
                    # Delete existing streams if overwrite is enabled
                    if overwrite:
                        ChannelStream.objects.filter(channel_id=channel_id).delete()
                    
                    # Create new stream assignments with quality-based ordering
                    existing_stream_ids = set(ChannelStream.objects.filter(channel_id=channel_id).values_list('stream_id', flat=True))
                    for order, stream_id in enumerate(stream_ids):
                        if not overwrite and stream_id in existing_stream_ids:
                            continue
                        ChannelStream.objects.create(channel_id=channel_id, stream_id=stream_id, order=order)
                    
                    success_count += 1
                    
                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Error assigning streams to channel {channel_data['channel_name']}: {e}")
                    error_count += 1
            
            logger.info(f"✅ [Stream-Mapparr] US OTA MATCHING COMPLETED")
            logger.info(f"[Stream-Mapparr] Successfully assigned: {success_count} channels")
            if error_count > 0:
                logger.warning(f"[Stream-Mapparr] Errors: {error_count} channels")
            logger.info(f"[Stream-Mapparr] CSV export: {csv_filepath}")
            
            return {
                "status": "success",
                "message": f"✅ US OTA Matching Complete\n\n"
                           f"📊 Matched {len(matched_channels)} channels\n"
                           f"✅ Successfully assigned: {success_count}\n"
                           f"{'⚠️ Errors: ' + str(error_count) if error_count > 0 else ''}\n"
                           f"📄 CSV: {csv_filename}"
            }
            
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error in US OTA matching: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "error", "message": f"Error in US OTA matching: {str(e)}"}

    def sort_streams_action(self, settings, logger, context=None):
        """Sort existing alternate streams by quality for all channels"""
        try:
            # Check dry run mode
            dry_run = settings.get('dry_run_mode', False)
            if isinstance(dry_run, str):
                dry_run = dry_run.lower() in ('true', 'yes', '1')
            
            mode_label = "DRY RUN (Preview)" if dry_run else "LIVE MODE"
            logger.info(f"[Stream-Mapparr] === SORT STREAMS ACTION STARTED ({mode_label}) ===")
            
            # Get settings
            profile_name = settings.get('profile_name', '').strip()
            if not profile_name:
                return {"status": "error", "message": "Profile name is required"}
            
            selected_groups_str = settings.get('selected_groups', '').strip()
            
            # Get API token
            token, error = self.get_or_refresh_api_token(settings, logger)
            if error:
                return {"status": "error", "message": error}
            
            # Fetch all channels for the profile
            logger.info(f"[Stream-Mapparr] Fetching channels for profile: {profile_name}")
            
            # Get profile IDs
            all_profiles = self._get_api_data("/api/channels/profiles/", token, settings, logger, None)
            profile_names = [p.strip() for p in profile_name.split(',')]
            profile_ids = []
            for profile in all_profiles:
                if profile['name'] in profile_names:
                    profile_ids.append(profile['id'])
            
            if not profile_ids:
                return {"status": "error", "message": f"Profile(s) not found: {profile_name}"}
            
            # Fetch channel groups if filtering is needed
            group_name_to_id = {}
            if selected_groups_str:
                logger.info(f"[Stream-Mapparr] Fetching channel groups for filtering...")
                all_groups = []
                page = 1
                while True:
                    try:
                        api_groups = self._get_api_data(f"/api/channels/groups/?page={page}", token, settings, logger, None)
                    except Exception as e:
                        if page > 1:
                            logger.debug(f"[Stream-Mapparr] No more group pages available (attempted page {page})")
                            break
                        else:
                            raise
                    
                    if isinstance(api_groups, dict) and 'results' in api_groups:
                        results = api_groups['results']
                        if not results:
                            break
                        all_groups.extend(results)
                        if not api_groups.get('next'):
                            break
                        page += 1
                    elif isinstance(api_groups, list):
                        if not api_groups:
                            break
                        all_groups.extend(api_groups)
                        break
                    else:
                        break
                
                group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}
            
            # Fetch ALL channels from API
            all_channels = self._get_api_data("/api/channels/channels/", token, settings, logger, None)
            logger.info(f"[Stream-Mapparr] Fetched {len(all_channels)} total channels from API")
            
            # Filter to channels in the specified profile(s) using Django ORM
            channels_in_profile = []
            for channel in all_channels:
                channel_id = channel['id']
                is_in_profile = ChannelProfileMembership.objects.filter(
                    channel_id=channel_id,
                    channel_profile_id__in=profile_ids,
                    enabled=True
                ).exists()
                if is_in_profile:
                    channels_in_profile.append(channel)
            
            logger.info(f"[Stream-Mapparr] Found {len(channels_in_profile)} channels in profile(s): {', '.join(profile_names)}")
            
            # Filter by channel groups if specified
            if selected_groups_str:
                selected_groups = [g.strip() for g in selected_groups_str.split(',') if g.strip()]
                valid_group_ids = [group_name_to_id[name] for name in selected_groups if name in group_name_to_id]
                
                if not valid_group_ids:
                    return {"status": "error", "message": f"None of the specified channel groups were found: {selected_groups_str}"}
                
                # Filter channels by group
                filtered_channels = [ch for ch in channels_in_profile if ch.get('channel_group_id') in valid_group_ids]
                channels_in_profile = filtered_channels
                logger.info(f"[Stream-Mapparr] Filtered to {len(channels_in_profile)} channels in groups: {', '.join(selected_groups)}")
            
            # Build M3U priority map if M3U sources are specified
            selected_m3us_str = settings.get('selected_m3us', '').strip()
            m3u_priority_map = {}
            if selected_m3us_str:
                # Fetch M3U sources
                try:
                    all_m3us = self._get_api_data("/api/m3u/accounts/", token, settings, logger, None)
                    m3u_name_to_id = {m['name']: m['id'] for m in all_m3us if 'name' in m and 'id' in m}
                    
                    selected_m3us = [m.strip() for m in selected_m3us_str.split(',') if m.strip()]
                    valid_m3u_ids = [m3u_name_to_id[name] for name in selected_m3us if name in m3u_name_to_id]
                    
                    if valid_m3u_ids:
                        # Create M3U ID to priority mapping (0 = highest priority)
                        m3u_priority_map = {m3u_id: idx for idx, m3u_id in enumerate(valid_m3u_ids)}
                        logger.info(f"[Stream-Mapparr] M3U priority order: {', '.join([f'{name} (priority {idx})' for idx, name in enumerate(selected_m3us)])}")
                except Exception as e:
                    logger.warning(f"[Stream-Mapparr] Could not fetch M3U sources for prioritization: {e}")
            
            # Get channels with multiple streams using Django ORM
            channels_with_multiple_streams = []
            for channel in channels_in_profile:
                channel_id = channel['id']
                # Query ChannelStream relationships to get stream count
                stream_ids = list(ChannelStream.objects.filter(channel_id=channel_id).order_by('order').values_list('stream_id', flat=True))
                
                if len(stream_ids) > 1:
                    # Fetch stream details including stats and M3U account
                    streams = []
                    for stream_id in stream_ids:
                        try:
                            stream = Stream.objects.get(id=stream_id)
                            
                            # Get M3U priority for this stream
                            m3u_account_id = stream.m3u_account_id
                            if m3u_account_id and m3u_account_id in m3u_priority_map:
                                m3u_priority = m3u_priority_map[m3u_account_id]
                            else:
                                # Stream not from a prioritized M3U source
                                m3u_priority = 999
                            
                            streams.append({
                                'id': stream.id,
                                'name': stream.name,
                                'stats': stream.stream_stats or {},
                                '_m3u_priority': m3u_priority
                            })
                        except Stream.DoesNotExist:
                            logger.warning(f"[Stream-Mapparr] Stream {stream_id} no longer exists, skipping")
                    
                    if len(streams) > 1:
                        channel['streams'] = streams
                        channels_with_multiple_streams.append(channel)
            
            if not channels_with_multiple_streams:
                message = "No channels found with multiple streams to sort."
                logger.info(f"[Stream-Mapparr] {message}")
                return {"status": "success", "message": message}
            
            # Sort streams for each channel
            sorted_count = 0
            changes = []
            already_sorted_count = 0
            
            for channel in channels_with_multiple_streams:
                channel_id = channel['id']
                channel_name = channel['name']
                streams = channel.get('streams', [])
                
                # Sort streams by quality
                sorted_streams = self._sort_streams_by_quality(streams)
                
                # Check if order changed
                original_ids = [s['id'] for s in streams]
                sorted_ids = [s['id'] for s in sorted_streams]
                
                if original_ids != sorted_ids:
                    sorted_count += 1
                    
                    # Log the reordering
                    stream_names = [s['name'] for s in sorted_streams]
                    logger.info(f"[Stream-Mapparr] Channel '{channel_name}': Reordered {len(sorted_streams)} streams")
                    logger.debug(f"[Stream-Mapparr]   Order: {' → '.join(stream_names[:3])}")
                    
                    changes.append({
                        'channel_id': channel_id,
                        'channel_name': channel_name,
                        'stream_count': len(sorted_streams),
                        'stream_names': stream_names
                    })
                    
                    # Apply changes if not dry run
                    if not dry_run:
                        # Delete existing ChannelStream relationships
                        ChannelStream.objects.filter(channel_id=channel_id).delete()
                        
                        # Create new relationships with correct order
                        for index, stream in enumerate(sorted_streams):
                            ChannelStream.objects.create(
                                channel_id=channel_id,
                                stream_id=stream['id'],
                                order=index
                            )
                else:
                    already_sorted_count += 1
            
            # Log summary with clarification
            logger.info(f"[Stream-Mapparr] Sorted streams for {sorted_count} channels ({already_sorted_count} already in correct order)")
            
            # Create CSV if dry run OR if export setting enabled
            create_csv = settings.get('enable_scheduled_csv_export', PluginConfig.DEFAULT_ENABLE_CSV_EXPORT)
            if isinstance(create_csv, str):
                create_csv = create_csv.lower() in ('true', 'yes', '1')
            
            csv_created = None
            if dry_run or create_csv:
                try:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"stream_mapparr_sorted_{timestamp}.csv" if not dry_run else f"stream_mapparr_preview_{timestamp}.csv"
                    filepath = os.path.join("/data/exports", filename)
                    os.makedirs("/data/exports", exist_ok=True)
                    
                    # Build processed_data dict for header generator
                    selected_groups = [g.strip() for g in selected_groups_str.split(',') if g.strip()] if selected_groups_str else []
                    processed_data_for_header = {
                        'profile_name': profile_name,
                        'selected_groups': selected_groups,
                        'selected_stream_groups': [],
                        'selected_m3us': [],
                        'ignore_tags': [],
                        'visible_channel_limit': 1,
                        'ignore_quality': True,
                        'ignore_regional': True,
                        'ignore_geographic': True,
                        'ignore_misc': True,
                        'filter_dead_streams': False
                    }
                    
                    with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                        # Write comprehensive header using standard generator
                        header_comment = self._generate_csv_header_comment(
                            settings,
                            processed_data_for_header,
                            action_name="Sort Alternate Streams",
                            is_scheduled=False,  # Sort Streams is always manual
                            total_visible_channels=sorted_count,
                            total_matched_streams=0
                        )
                        csvfile.write(header_comment)
                        
                        fieldnames = ['channel_id', 'channel_name', 'stream_count', 'stream_names']
                        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                        writer.writeheader()
                        
                        for change in changes:
                            writer.writerow({
                                'channel_id': change['channel_id'],
                                'channel_name': change['channel_name'],
                                'stream_count': change['stream_count'],
                                'stream_names': '; '.join(change['stream_names'])
                            })
                    
                    logger.info(f"[Stream-Mapparr] 📄 CSV EXPORT CREATED: {filepath}")
                    csv_created = filepath
                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Failed to create CSV export: {e}")
            
            # Build success message
            if dry_run:
                message = f"✅ PREVIEW COMPLETED (DRY RUN)\n\nWould sort {sorted_count} channel(s) with multiple streams."
            else:
                message = f"✅ SORT STREAMS COMPLETED\n\nSorted {sorted_count} channel(s) with multiple streams."
                # Trigger frontend refresh
                self._trigger_frontend_refresh(settings, logger)
            
            if csv_created:
                message += f"\n📄 Report: {csv_created}"
            
            logger.info(f"[Stream-Mapparr] {message}")
            
            return {"status": "success", "message": message}
            
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error in sort_streams_action: {str(e)}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {"status": "error", "message": f"Error sorting streams: {str(e)}"}

    def manage_channel_visibility_action(self, settings, logger, context=None):
        """Disable all channels, then enable only channels with 1 or more streams."""
        if not os.path.exists(self.processed_data_file):
            return {"status": "error", "message": "No processed data found. Please run 'Load/Process Channels' first."}

        try:
            self._send_progress_update("manage_channel_visibility", 'running', 5, 'Initializing...', context)
            
            self._send_progress_update("manage_channel_visibility", 'running', 10, 'Loading channel data...', context)
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)

            profile_id = processed_data.get('profile_id')
            channels = processed_data.get('channels', [])
            channels_data = self._load_channels_data(logger, settings)

            # Step 1: Get stream counts
            self._send_progress_update("manage_channel_visibility", 'running', 20, 'Counting streams...', context)
            channel_stream_counts = {}
            for channel in channels:
                stream_count = ChannelStream.objects.filter(channel_id=channel['id']).count()
                channel_stream_counts[channel['id']] = {'name': channel['name'], 'stream_count': stream_count}

            # Step 2: Disable all channels using Django ORM
            self._send_progress_update("manage_channel_visibility", 'running', 40, f'Disabling all {len(channels)} channels...', context)
            logger.info(f"[Stream-Mapparr] Disabling all {len(channels)} channels using Django ORM...")

            channel_ids = [ch['id'] for ch in channels]
            ChannelProfileMembership.objects.filter(
                channel_profile_id=profile_id,
                channel_id__in=channel_ids
            ).update(enabled=False)
            
            logger.info(f"[Stream-Mapparr] Disabled {len(channel_ids)} channels in profile {profile_id}")

            # Step 3: Determine channels to enable
            self._send_progress_update("manage_channel_visibility", 'running', 60, 'Determining channels to enable...', context)
            channels_to_enable = []
            channel_groups = {}
            
            # Reuse grouping logic
            ignore_tags = processed_data.get('ignore_tags', [])
            ignore_quality = processed_data.get('ignore_quality', True)
            ignore_regional = processed_data.get('ignore_regional', True)
            ignore_geographic = processed_data.get('ignore_geographic', True)
            ignore_misc = processed_data.get('ignore_misc', True)
            filter_dead = processed_data.get('filter_dead_streams', PluginConfig.DEFAULT_FILTER_DEAD_STREAMS)

            for channel in channels:
                channel_info = self._get_channel_info_from_json(channel['name'], channels_data, logger)
                if self._is_ota_channel(channel_info):
                    callsign = channel_info.get('callsign', '')
                    group_key = f"OTA_{callsign}" if callsign else self._clean_channel_name(channel['name'], ignore_tags)
                else:
                    group_key = self._clean_channel_name(channel['name'], ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc)
                if group_key not in channel_groups: 
                    channel_groups[group_key] = []
                channel_groups[group_key].append(channel)

            for group_key, group_channels in channel_groups.items():
                sorted_channels = self._sort_channels_by_priority(group_channels)
                enabled_in_group = False
                for ch in sorted_channels:
                    stream_count = channel_stream_counts[ch['id']]['stream_count']
                    is_attached = ch.get('attached_channel_id') is not None

                    if not is_attached and not enabled_in_group and stream_count >= 1:
                        channels_to_enable.append(ch['id'])
                        enabled_in_group = True

            # Step 4: Enable channels using Django ORM
            self._send_progress_update("manage_channel_visibility", 'running', 80, f'Enabling {len(channels_to_enable)} channels...', context)
            logger.info(f"[Stream-Mapparr] Enabling {len(channels_to_enable)} channels using Django ORM...")

            if channels_to_enable:
                ChannelProfileMembership.objects.filter(
                    channel_profile_id=profile_id,
                    channel_id__in=channels_to_enable
                ).update(enabled=True)
                
                logger.info(f"[Stream-Mapparr] Enabled {len(channels_to_enable)} channels in profile {profile_id}")

            self._trigger_frontend_refresh(settings, logger)
            
            success_msg = f"Visibility managed. Enabled {len(channels_to_enable)} of {len(channels)} channels."
            self._send_progress_update("manage_channel_visibility", 'success', 100, success_msg, context)
            return {"status": "success", "message": success_msg}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error managing visibility: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return {"status": "error", "message": f"Error: {str(e)}"}

    def clear_csv_exports_action(self, settings, logger):
        """Delete all CSV export files created by this plugin"""
        try:
            export_dir = "/data/exports"
            if not os.path.exists(export_dir):
                return {"status": "success", "message": "No export directory found."}

            deleted_count = 0
            for filename in os.listdir(export_dir):
                if filename.startswith("stream_mapparr_") and filename.endswith(".csv"):
                    try:
                        os.remove(os.path.join(export_dir, filename))
                        deleted_count += 1
                    except Exception: pass

            return {"status": "success", "message": f"Deleted {deleted_count} CSV files."}
        except Exception as e:
            return {"status": "error", "message": f"Error clearing CSV exports: {e}"}

    def clear_operation_lock_action(self, settings, logger):
        """Manually clear the operation lock file"""
        try:
            lock_file = PluginConfig.OPERATION_LOCK_FILE
            
            if not os.path.exists(lock_file):
                return {"status": "success", "message": "No operation lock found."}
            
            # Read lock info before deleting
            try:
                with open(lock_file, 'r') as f:
                    lock_data = json.load(f)
                action_name = lock_data.get('action', 'unknown')
                lock_time_str = lock_data.get('start_time')
                if lock_time_str:
                    lock_time = datetime.fromisoformat(lock_time_str)
                    age_minutes = (datetime.now() - lock_time).total_seconds() / 60
                    lock_info = f"{action_name} (started {age_minutes:.1f} minutes ago)"
                else:
                    lock_info = action_name
            except:
                lock_info = "unknown operation"
            
            # Delete the lock file
            os.remove(lock_file)
            logger.info(f"[Stream-Mapparr] Operation lock cleared manually: {lock_info}")
            
            return {
                "status": "success",
                "message": f"✅ Operation lock cleared.\n\nRemoved lock for: {lock_info}\n\nYou can now run operations normally."
            }
        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error clearing operation lock: {e}")
            return {"status": "error", "message": f"Error clearing lock: {e}"}

