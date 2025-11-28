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

# Django model imports - same approach as Event Channel Managarr
from apps.channels.models import Channel, ChannelProfileMembership, ChannelStream

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

class SmartRateLimiter:
    """
    Handles rate limiting with exponential backoff for 429/5xx errors.
    Uses standard library only.
    """
    def __init__(self, setting_value="medium", logger=None):
        self.logger = logger
        self.disabled = setting_value == "none"

        # Define delays (seconds) based on settings
        if self.disabled:
            self.base_delay = 0.0    # No rate limiting
        elif setting_value == "high":
            self.base_delay = 2.0    # 1 request every 2 seconds
        elif setting_value == "low":
            self.base_delay = 0.1    # 10 requests per second
        else:
            self.base_delay = 0.5    # 2 requests per second (Default/Medium)

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
            backoff = min(60, self.base_delay * (2 ** self.consecutive_errors))
            jitter = backoff * 0.1 * random.random() # +/- 10% jitter
            self.current_delay = backoff + jitter

            if self.logger:
                self.logger.warning(f"[Stream-Mapparr] Rate limit/Server error ({status_code}). Backing off to {self.current_delay:.2f}s")
        else:
            self.current_delay = self.base_delay

class Plugin:
    """Dispatcharr Stream-Mapparr Plugin"""

    name = "Stream-Mapparr"
    version = "0.6.0b"
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
                "default": True,
                "help_text": "If enabled, all existing streams will be removed and replaced with matched streams. If disabled, only new streams will be added (existing streams preserved).",
            },
            {
                "id": "fuzzy_match_threshold",
                "label": "🎯 Fuzzy Match Threshold",
                "type": "number",
                "default": 85,
                "help_text": "Minimum similarity score (0-100) for fuzzy matching. Higher values require closer matches. Default: 85",
            },
            {
                "id": "dispatcharr_url",
                "label": "🌐 Dispatcharr URL",
                "type": "string",
                "default": "",
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
                "default": "",
                "placeholder": "Sports, Movies, News",
                "help_text": "*** Required Field *** - The name(s) of existing Channel Profile(s) to process channels from. Multiple profiles can be specified separated by commas.",
            },
            {
                "id": "selected_groups",
                "label": "📁 Channel Groups (comma-separated)",
                "type": "string",
                "default": "",
                "placeholder": "Sports, News, Entertainment",
                "help_text": "Specific channel groups to process, or leave empty for all groups.",
            },
            {
                "id": "ignore_tags",
                "label": "🏷️ Ignore Tags (comma-separated)",
                "type": "string",
                "default": "",
                "placeholder": "4K, [4K], \" East\", \"[Dead]\"",
                "help_text": "Tags to ignore when matching streams. Use quotes to preserve spaces/special chars (e.g., \" East\" for tags with leading space).",
            },
            {
                "id": "ignore_quality_tags",
                "label": "🎬 Ignore Quality Tags",
                "type": "boolean",
                "default": True,
                "help_text": "If enabled, all quality indicators will be ignored in any format and position (e.g., 4K, [4K], (4K), FHD, [FHD], (FHD), HD, SD at beginning, middle, or end of name).",
            },
            {
                "id": "ignore_regional_tags",
                "label": "🌍 Ignore Regional Tags",
                "type": "boolean",
                "default": True,
                "help_text": "If enabled, hardcoded regional tags like 'East' will be ignored during matching.",
            },
            {
                "id": "ignore_geographic_tags",
                "label": "🗺️ Ignore Geographic Tags",
                "type": "boolean",
                "default": True,
                "help_text": "If enabled, all country codes will be ignored during matching (e.g., US, USA, US:, |FR|, FR -, [UK], etc.).",
            },
            {
                "id": "ignore_misc_tags",
                "label": "🏷️ Ignore Miscellaneous Tags",
                "type": "boolean",
                "default": True,
                "help_text": "If enabled, all content within parentheses will be ignored during matching (e.g., (CX), (B), (PRIME), (Backup)).",
            },
            {
                "id": "visible_channel_limit",
                "label": "👁️ Visible Channel Limit",
                "type": "number",
                "default": 1,
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
                "default": "medium",
                "help_text": "Controls delay between API calls. None=No delays, Low=Fast/Aggressive, Medium=Standard, High=Slow/Safe.",
            },
            {
                "id": "timezone",
                "label": "🌍 Timezone",
                "type": "select",
                "default": "US/Central",
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
                "id": "scheduled_times",
                "label": "⏰ Scheduled Run Times (24-hour format)",
                "type": "string",
                "default": "",
                "placeholder": "0600,1300,1800",
                "help_text": "Comma-separated times to run automatically each day (24-hour format). Example: 0600,1300,1800 runs at 6 AM, 1 PM, and 6 PM daily. Leave blank to disable scheduling.",
            },
            {
                "id": "enable_scheduled_csv_export",
                "label": "📄 Enable CSV Export",
                "type": "boolean",
                "default": True,
                "help_text": "If enabled, a CSV file of the scan results will be created when streams are added.",
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
            "id": "preview_changes",
            "label": "👀 Preview Changes (Dry Run)",
            "description": "Preview which streams will be added to channels (automatically loads channels if needed)",
        },
        {
            "id": "add_streams_to_channels",
            "label": "✅ Add Stream(s) to Channels",
            "description": "Add matching streams to channels (automatically loads channels if needed)",
            "confirm": {
                "required": True,
                "title": "Add Streams to Channels?",
                "message": "This will add and/or update streams for matching channels. Are you sure you want to continue?"
            }
        },
        {
            "id": "manage_channel_visibility",
            "label": "👁️ Manage Channel Visibility",
            "description": "Disable all channels, then enable only channels with 1 or more streams (excluding channels attached to others)",
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
    ]

    CHANNEL_QUALITY_TAG_ORDER = ["[4K]", "[UHD]", "[FHD]", "[HD]", "[SD]", "[Unknown]", "[Slow]", ""]

    STREAM_QUALITY_ORDER = [
        "[4K]", "(4K)", "4K", "[UHD]", "(UHD)", "UHD",
        "[FHD]", "(FHD)", "FHD",
        "[HD]", "(HD)", "HD", "(H)",
        "[SD]", "(SD)", "SD",
        "(F)",
        "(D)",
        "Slow", "[Slow]", "(Slow)"
    ]

    def __init__(self):
        # -- SINGLETON GUARD --
        # Ensure init logic runs only once even if Dispatcharr instantiates multiple times
        if getattr(self, '_initialized', False):
            return
        self._initialized = True

        self.processed_data_file = "/data/stream_mapparr_processed.json"
        self.version_check_cache_file = "/data/stream_mapparr_version_check.json"
        self.settings_file = "/data/stream_mapparr_settings.json"
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
                    LOGGER.info("[Stream-Mapparr] Loaded saved settings")
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
            logger.info(f"[Stream-Mapparr] Update Schedule - scheduled_times value: '{scheduled_times_str}'")

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
        
        # Otherwise use US/Central as default
        LOGGER.info("Using default timezone: US/Central")
        return "US/Central"
        
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
                LOGGER.error(f"[Stream-Mapparr] Unknown timezone: {tz_str}, falling back to America/Chicago")
                local_tz = pytz.timezone('America/Chicago')

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
                        
                        # Run if within 30 seconds and have not run today for this time
                        if -30 <= time_diff <= 30 and last_run.get(scheduled_time) != current_date:
                            LOGGER.info(f"[Stream-Mapparr] Scheduled scan triggered at {now.strftime('%Y-%m-%d %H:%M %Z')}")
                            try:
                                # Step 1: Load/Process Channels
                                LOGGER.info("[Stream-Mapparr] Step 1/2: Loading and processing channels...")
                                load_result = self.load_process_channels_action(settings, LOGGER)
                                
                                if load_result.get("status") == "success":
                                    LOGGER.info(f"[Stream-Mapparr] {load_result.get('message', 'Channels loaded successfully')}")
                                    
                                    # Step 2: Add Streams to Channels
                                    LOGGER.info("[Stream-Mapparr] Step 2/2: Adding streams to channels...")
                                    add_result = self.add_streams_to_channels_action(settings, LOGGER, is_scheduled=True)
                                    
                                    if add_result.get("status") == "success":
                                        LOGGER.info(f"[Stream-Mapparr] {add_result.get('message', 'Streams added successfully')}")
                                        LOGGER.info("[Stream-Mapparr] Scheduled stream mapping completed successfully")
                                    else:
                                        LOGGER.error(f"[Stream-Mapparr] Failed to add streams: {add_result.get('message', 'Unknown error')}")
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
                    
                    # Sleep for 30 seconds
                    _stop_event.wait(30)
                    
                except Exception as e:
                    LOGGER.error(f"[Stream-Mapparr] Error in scheduler loop: {e}")
                    _stop_event.wait(60)
        
        _bg_thread = threading.Thread(target=scheduler_loop, name="stream-mapparr-scheduler", daemon=True)
        _bg_thread.start()
        LOGGER.info(f"[Stream-Mapparr] Background scheduler started for times: {[t.strftime('%H:%M') for t in scheduled_times]}")

    def _stop_background_scheduler(self):
        """Stop background scheduler thread"""
        global _bg_thread
        if _bg_thread and _bg_thread.is_alive():
            LOGGER.info("Stopping background scheduler")
            _stop_event.set()
            _bg_thread.join(timeout=5)
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
                        if time_diff < timedelta(hours=24):
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
                LOGGER.info(f"[Stream-Mapparr] Initialized FuzzyMatcher with threshold: {match_threshold}")
            except Exception as e:
                LOGGER.warning(f"[Stream-Mapparr] Failed to initialize FuzzyMatcher: {e}")
                self.fuzzy_matcher = None

    def get_or_refresh_api_token(self, settings, logger):
        """Get API token from cache or refresh if expired."""
        if self.api_token and self.token_expiration and self.token_expiration > datetime.now():
            logger.info("[Stream-Mapparr] Using cached API token.")
            return self.api_token, None

        logger.info("[Stream-Mapparr] API token is expired or not found, getting a new one.")
        token, error = self._get_api_token(settings, logger)
        if token:
            self.api_token = token
            self.token_expiration = datetime.now() + timedelta(minutes=30)
            logger.info("[Stream-Mapparr] API token cached for 30 minutes.")

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

            logger.info(f"[Stream-Mapparr] Attempting to authenticate with Dispatcharr at: {url}")
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

            logger.info("[Stream-Mapparr] Successfully obtained API access token")
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
            response = requests.get(url, headers=headers, timeout=30)

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
                response = requests.get(url, headers=headers, timeout=30)

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
            json_data = response.json()

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
            logger.info(f"[Stream-Mapparr] Making API PATCH request to: {endpoint}")
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
            return response.json()

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
            logger.info(f"[Stream-Mapparr] Making API POST request to: {endpoint}")
            response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code == 401:
                logger.error("[Stream-Mapparr] API token expired or invalid, attempting to refresh.")
                self.api_token = None # Invalidate token
                new_token, error = self.get_or_refresh_api_token(settings, logger)
                if error:
                    raise Exception("Failed to refresh API token.")

                # Retry request with new token
                headers['Authorization'] = f'Bearer {new_token}'
                response = requests.post(url, headers=headers, json=payload, timeout=30)

            if response.status_code == 403:
                logger.error("[Stream-Mapparr] API access forbidden")
                raise Exception("API access forbidden. Check user permissions.")
            elif response.status_code == 404:
                logger.error(f"[Stream-Mapparr] API endpoint not found: {endpoint}")
                raise Exception(f"API endpoint not found: {endpoint}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"[Stream-Mapparr] API POST request failed for {endpoint}: {e}")
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
                logger.info("[Stream-Mapparr] Frontend refresh triggered via WebSocket")
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
                    logger.info(f"[Stream-Mapparr] Loaded {len(channels_list)} channels from {db_label}")

                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Error loading {channel_file}: {e}")

            logger.info(f"[Stream-Mapparr] Loaded total of {len(channels_data)} channels from {len(enabled_databases)} enabled database(s)")

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

    def _match_streams_to_channel(self, channel, all_streams, logger, ignore_tags=None,
                                  ignore_quality=True, ignore_regional=True, ignore_geographic=True,
                                  ignore_misc=True, channels_data=None):
        """Find matching streams for a channel using fuzzy matching when available."""
        if ignore_tags is None:
            ignore_tags = []
        if channels_data is None:
            channels_data = []

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

            for stream in all_streams:
                if re.search(callsign_pattern, stream['name'], re.IGNORECASE):
                    matching_streams.append(stream)

            if matching_streams:
                sorted_streams = self._sort_streams_by_quality(matching_streams)
                cleaned_stream_names = [self._clean_channel_name(
                    s['name'], ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                ) for s in sorted_streams]
                return sorted_streams, cleaned_channel_name, cleaned_stream_names, "Callsign match", database_used

        # Use fuzzy matching if available
        if self.fuzzy_matcher:
            stream_names = [stream['name'] for stream in all_streams]
            matched_stream_name, score, match_type = self.fuzzy_matcher.fuzzy_match(
                channel_name, stream_names, ignore_tags, remove_cinemax=channel_has_max
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
                for stream in all_streams:
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
                    
                    # Remove very short tokens (1 char only) but keep 2-char tokens like "al"
                    stream_tokens = {t for t in stream_tokens if len(t) > 1}
                    channel_tokens = {t for t in channel_tokens if len(t) > 1}
                    
                    # Check if there's significant overlap
                    if stream_tokens and channel_tokens:
                        common_tokens = stream_tokens & channel_tokens
                        overlap_ratio = len(common_tokens) / min(len(stream_tokens), len(channel_tokens))
                        
                        # Require either multiple common tokens OR high overlap ratio
                        # This prevents false matches like "Gol TV English" matching "Al Jazeera English"
                        has_multiple_common = len(common_tokens) >= 2
                        has_high_overlap = overlap_ratio >= 0.75
                        
                        if has_multiple_common or has_high_overlap:
                            # Calculate full string similarity
                            similarity = self.fuzzy_matcher.calculate_similarity(stream_lower, channel_lower)
                            if int(similarity * 100) >= self.fuzzy_matcher.match_threshold:
                                matching_streams.append(stream)

                if matching_streams:
                    sorted_streams = self._sort_streams_by_quality(matching_streams)
                    cleaned_stream_names = [self._clean_channel_name(
                        s['name'], ignore_tags, ignore_quality, ignore_regional,
                        ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                    ) for s in sorted_streams]
                    return sorted_streams, cleaned_channel_name, cleaned_stream_names, f"Fuzzy match ({match_type}, score: {score})", database_used

            return [], cleaned_channel_name, [], "No fuzzy match", database_used

        # Fallback to basic substring matching if fuzzy matcher unavailable
        matching_streams = []

        if not all_streams:
            return [], cleaned_channel_name, [], "No streams available", database_used

        # Try exact channel name matching from JSON first
        if channel_info and channel_info.get('channel_name'):
            json_channel_name = channel_info['channel_name']
            for stream in all_streams:
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
                cleaned_stream_names = [self._clean_channel_name(
                    s['name'], ignore_tags, ignore_quality, ignore_regional,
                    ignore_geographic, ignore_misc, remove_cinemax=channel_has_max
                ) for s in sorted_streams]
                return sorted_streams, cleaned_channel_name, cleaned_stream_names, "Exact match (channels.json)", database_used

        # Fallback to basic substring matching
        for stream in all_streams:
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
                    channel_name, stream_names, ignore_tags, remove_cinemax=channel_has_max
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
            
            # Use a notification type pattern similar to existing Dispatcharr notifications
            # Format follows: plugin_action_result pattern
            notification_data = {
                'type': 'plugin_notification',
                'plugin': 'stream-mapparr',
                'action': action_id,
                'success': is_success,
                'message': message,
                'title': 'Stream-Mapparr' if is_success else 'Stream-Mapparr Error'
            }
            
            # Add error field for failures
            if not is_success:
                notification_data['error'] = message
            
            LOGGER.info(f"[Stream-Mapparr] Sending notification: {action_id} ({'success' if is_success else 'error'}) - {message}")
            send_websocket_update('updates', 'update', notification_data)
            
        except Exception as e:
            LOGGER.warning(f"[Stream-Mapparr] Failed to send notification: {e}")

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
            LOGGER.info(f"[Stream-Mapparr] Saving settings with keys: {list(settings.keys())}")

            # Get timezone and schedule settings
            user_timezone = settings.get("timezone") or "US/Central"
            enabled = settings.get("schedule_enabled", False)
            if isinstance(enabled, str):
                enabled = enabled.lower() in ('true', '1', 'yes', 'on')

            cron_schedule = settings.get("schedule_cron") or ""
            cron_schedule = cron_schedule.strip() if cron_schedule else ""

            LOGGER.info(f"[Stream-Mapparr] Schedule settings: enabled={enabled}, cron='{cron_schedule}', tz={user_timezone}")

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
            match_threshold = settings.get("fuzzy_match_threshold", 85)
            try:
                match_threshold = int(match_threshold)
            except (ValueError, TypeError):
                match_threshold = 85

            self._initialize_fuzzy_matcher(match_threshold)

            # Actions that should run in background to avoid timeout
            # Note: load_process_channels is internal-only (called by preview/add actions)
            background_actions = {
                "load_process_channels": self.load_process_channels_action,
                "preview_changes": self.preview_changes_action,
                "add_streams_to_channels": self.add_streams_to_channels_action,
                "manage_channel_visibility": self.manage_channel_visibility_action,
            }
            
            # Actions that run immediately (synchronous)
            immediate_actions = {
                "validate_settings": self.validate_settings_action,
                "update_schedule": self.update_schedule_action,
                "cleanup_periodic_tasks": self.cleanup_periodic_tasks_action,
                "clear_csv_exports": self.clear_csv_exports_action,
            }

            if action in background_actions:
                # Run synchronously to keep buttons disabled until complete
                # The frontend will keep buttons disabled until this returns
                try:
                    self._send_progress_update(action, 'running', 0, 'Starting operation...', context)
                    result = background_actions[action](settings, logger, context)
                    
                    if result.get('status') == 'success':
                        self._send_progress_update(action, 'completed', 100, 
                                                  result.get('message', 'Operation completed successfully'), context)
                        return result
                    else:
                        self._send_progress_update(action, 'error', 0, 
                                                  result.get('message', 'Operation failed'), context)
                        return result
                        
                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Operation failed: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
                    error_msg = f'Error: {str(e)}'
                    self._send_progress_update(action, 'error', 0, error_msg, context)
                    return {'status': 'error', 'message': error_msg}
            
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
            logger.info("[Stream-Mapparr] Validating API connection...")
            token, error = self.get_or_refresh_api_token(settings, logger)
            if error:
                validation_results.append(f"❌ API Connection: {error}")
                has_errors = True
                return has_errors, validation_results, token
            else:
                validation_results.append("✅ API Connection")

            # 2. Validate profile name exists
            logger.info("[Stream-Mapparr] Validating profile names...")
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
            logger.info("[Stream-Mapparr] Validating channel groups...")
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
                            logger.info(f"[Stream-Mapparr] No more group pages available (attempted page {page})")
                            break
                        else:
                            # If error on first page, re-raise
                            raise

                    if isinstance(api_groups, dict) and 'results' in api_groups:
                        results = api_groups['results']
                        if not results:
                            logger.info("[Stream-Mapparr] Reached last page of groups (empty results)")
                            break
                        all_groups.extend(results)
                        if not api_groups.get('next'):
                            break
                        page += 1
                    elif isinstance(api_groups, list):
                        if not api_groups:
                            logger.info("[Stream-Mapparr] Reached last page of groups (empty results)")
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
            logger.info("[Stream-Mapparr] Validating timezone...")
            timezone_str = settings.get("timezone") or "US/Central"
            timezone_str = timezone_str.strip() if timezone_str else "US/Central"
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
            logger.info("[Stream-Mapparr] Validating channel databases...")
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
            user_timezone = settings.get("timezone") or "US/Central"
            enabled = settings.get("schedule_enabled", False)
            if isinstance(enabled, str):
                enabled = enabled.lower() in ('true', '1', 'yes', 'on')

            cron_schedule = settings.get("schedule_cron") or ""
            cron_schedule = cron_schedule.strip() if cron_schedule else ""

            logger.info(f"[Stream-Mapparr] Syncing schedule: enabled={enabled}, schedule='{cron_schedule}', tz={user_timezone}")

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
            user_timezone = settings.get("timezone", "America/Chicago")
            logger.info(f"[Stream-Mapparr] Viewing schedules with timezone: {user_timezone}")

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
            limiter = SmartRateLimiter(settings.get("rate_limiting", "medium"), logger)

            self._send_progress_update("load_process_channels", 'running', 5, 'Validating settings...', context)
            logger.info("[Stream-Mapparr] Validating settings before loading channels...")
            has_errors, validation_results, token = self._validate_plugin_settings(settings, logger)

            if has_errors:
                return {"status": "error", "message": "Cannot load channels - validation failed."}

            self._send_progress_update("load_process_channels", 'running', 10, 'Settings validated, loading data...', context)
            logger.info("[Stream-Mapparr] Settings validated successfully, proceeding with channel load...")

            profile_names_str = settings.get("profile_name") or ""
            profile_names_str = profile_names_str.strip() if profile_names_str else ""
            selected_groups_str = settings.get("selected_groups") or ""
            selected_groups_str = selected_groups_str.strip() if selected_groups_str else ""
            ignore_tags_str = settings.get("ignore_tags") or ""
            ignore_tags_str = ignore_tags_str.strip() if ignore_tags_str else ""
            visible_channel_limit_str = settings.get("visible_channel_limit", "1")
            visible_channel_limit = int(visible_channel_limit_str) if visible_channel_limit_str else 1

            ignore_quality = settings.get("ignore_quality_tags", True)
            ignore_regional = settings.get("ignore_regional_tags", True)
            ignore_geographic = settings.get("ignore_geographic_tags", True)
            ignore_misc = settings.get("ignore_misc_tags", True)

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
                        logger.info(f"[Stream-Mapparr] No more group pages available (attempted page {page})")
                        break
                    else:
                        # If error on first page, re-raise
                        raise

                if isinstance(api_groups, dict) and 'results' in api_groups:
                    results = api_groups['results']
                    if not results:
                        logger.info("[Stream-Mapparr] Reached last page of groups (empty results)")
                        break
                    all_groups.extend(results)
                    if not api_groups.get('next'):
                        break
                    page += 1
                elif isinstance(api_groups, list):
                    if not api_groups:
                        logger.info("[Stream-Mapparr] Reached last page of groups (empty results)")
                        break
                    all_groups.extend(api_groups)
                    break
                else:
                    break

            group_name_to_id = {g['name']: g['id'] for g in all_groups if 'name' in g and 'id' in g}

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
                        logger.info(f"[Stream-Mapparr] No more pages available (attempted page {page})")
                        break
                    else:
                        # If error on first page, re-raise
                        raise

                # Handle both paginated and non-paginated responses
                if isinstance(streams_response, dict) and 'results' in streams_response:
                    results = streams_response['results']

                    # Check if we got empty results
                    if not results:
                        logger.info("[Stream-Mapparr] Reached last page of streams (empty results)")
                        break

                    all_streams_data.extend(results)
                    logger.debug(f"[Stream-Mapparr] Fetched page {page}: {len(results)} streams (total so far: {len(all_streams_data)})")

                    # Stop if this page had fewer results than page_size (last page)
                    if len(results) < 100:
                        logger.info("[Stream-Mapparr] Reached last page of streams")
                        break

                    page += 1
                elif isinstance(streams_response, list):
                    # Check if we got empty results
                    if not streams_response:
                        logger.info("[Stream-Mapparr] Reached last page of streams (empty results)")
                        break

                    # List response - could still be paginated
                    all_streams_data.extend(streams_response)
                    logger.debug(f"[Stream-Mapparr] Fetched page {page}: {len(streams_response)} streams (total so far: {len(all_streams_data)})")

                    # If we got exactly 100 results, there might be more pages
                    if len(streams_response) == 100:
                        page += 1
                    else:
                        logger.info("[Stream-Mapparr] Reached last page of streams")
                        break
                else:
                    logger.warning("[Stream-Mapparr] Unexpected streams response format")
                    break

            self.loaded_channels = channels_to_process
            self.loaded_streams = all_streams_data

            processed_data = {
                "loaded_at": datetime.now().isoformat(),
                "profile_name": profile_names_str,
                "profile_names": profile_names,
                "profile_id": profile_id,
                "profile_ids": profile_ids,
                "selected_groups": selected_groups,
                "ignore_tags": ignore_tags,
                "visible_channel_limit": visible_channel_limit,
                "ignore_quality": ignore_quality,
                "ignore_regional": ignore_regional,
                "ignore_geographic": ignore_geographic,
                "ignore_misc": ignore_misc,
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

    def _generate_csv_header_comment(self, settings, processed_data, total_visible_channels=0, total_matched_streams=0, low_match_channels=None, threshold_data=None):
        """Generate CSV comment header with plugin version and settings info."""
        # Debug: Log all settings keys to see what's available
        LOGGER.info(f"[Stream-Mapparr] CSV generation - All settings keys: {list(settings.keys())}")
        
        profile_name = processed_data.get('profile_name', 'N/A')
        selected_groups = processed_data.get('selected_groups', [])
        current_threshold = settings.get('fuzzy_match_threshold', 85)
        
        # Build header with all settings except login credentials
        header_lines = [
            f"# Stream-Mapparr Export v{self.version}",
            f"# Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "#",
            "# === Profile & Group Settings ===",
            f"# Profile Name(s): {profile_name}",
            f"# Selected Groups: {', '.join(selected_groups) if selected_groups else '(all groups)'}",
            "#",
            "# === Matching Settings ===",
            f"# Fuzzy Match Threshold: {current_threshold}",
            f"# Overwrite Streams: {settings.get('overwrite_streams', True)}",
            f"# Visible Channel Limit: {processed_data.get('visible_channel_limit', 1)}",
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
            "# === Scheduling Settings ===",
            f"# Timezone: {settings.get('timezone', 'US/Central')}",
            f"# Scheduled Times: {settings.get('scheduled_times', '(none)')}",
            f"# Enable Scheduled CSV Export: {settings.get('enable_scheduled_csv_export', False)}",
            "#",
            "# === API Settings ===",
            f"# Rate Limiting: {settings.get('rate_limiting', 'medium')}",
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
                    f"# Consider lowering Fuzzy Match Threshold from {current_threshold} to {lowest_threshold_with_matches} for better results.",
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
                f"# Consider lowering Fuzzy Match Threshold for better results.",
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
        # Auto-load channels if not already loaded
        if not os.path.exists(self.processed_data_file):
            logger.info("[Stream-Mapparr] No processed data found, loading channels automatically...")
            self._send_progress_update("preview_changes", 'running', 0, 'Loading channels and streams...', context)
            load_result = self.load_process_channels_action(settings, logger, context)
            if load_result.get('status') != 'success':
                return load_result

        try:
            self._send_progress_update("preview_changes", 'running', 5, 'Initializing preview...', context)
            limiter = SmartRateLimiter(settings.get("rate_limiting", "medium"), logger)

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
            current_threshold = settings.get('fuzzy_match_threshold', 85)
            try:
                current_threshold = int(current_threshold)
            except (ValueError, TypeError):
                current_threshold = 85

            self._send_progress_update("preview_changes", 'running', 30, f'Analyzing {len(channel_groups)} channel groups...', context)
            
            processed_groups = 0
            total_groups = len(channel_groups)
            
            for group_key, group_channels in channel_groups.items():
                limiter.wait()
                
                # Update progress
                processed_groups += 1
                progress = 30 + int((processed_groups / total_groups) * 50)  # 30-80%
                if processed_groups % max(1, total_groups // 10) == 0:  # Update every 10%
                    self._send_progress_update("preview_changes", 'running', progress, 
                                              f'Processing channel group {processed_groups}/{total_groups}...', context)
                
                sorted_channels = self._sort_channels_by_priority(group_channels)
                
                # Get matches at current threshold for the primary channel
                matched_streams, cleaned_channel_name, cleaned_stream_names, match_reason, database_used = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc, channels_data
                )

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

            self._send_progress_update("preview_changes", 'running', 85, 'Generating CSV report...', context)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"stream_mapparr_preview_{timestamp}.csv"
            filepath = os.path.join("/data/exports", filename)
            os.makedirs("/data/exports", exist_ok=True)

            with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
                header_comment = self._generate_csv_header_comment(settings, processed_data, 
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

            message = f"Preview complete. {total_channels_to_update} channels will be updated. Report saved to {filepath}"
            self._send_progress_update("preview_changes", 'success', 100, message, context)
            return {"status": "success", "message": message}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error previewing changes: {str(e)}")
            return {"status": "error", "message": f"Error previewing changes: {str(e)}"}

    def add_streams_to_channels_action(self, settings, logger, is_scheduled=False, context=None):
        """Add matching streams to channels and replace existing stream assignments."""
        # Auto-load channels if not already loaded
        if not os.path.exists(self.processed_data_file):
            logger.info("[Stream-Mapparr] No processed data found, loading channels automatically...")
            self._send_progress_update("add_streams_to_channels", 'running', 0, 'Loading channels and streams...', context)
            load_result = self.load_process_channels_action(settings, logger, context)
            if load_result.get('status') != 'success':
                return load_result

        try:
            self._send_progress_update("add_streams_to_channels", 'running', 5, 'Initializing stream assignment...', context)
            limiter = SmartRateLimiter(settings.get("rate_limiting", "medium"), logger)
            
            self._send_progress_update("add_streams_to_channels", 'running', 10, 'Authenticating...', context)
            token, error = self._get_api_token(settings, logger)
            if error: return {"status": "error", "message": error}

            channels_data = self._load_channels_data(logger, settings)
            with open(self.processed_data_file, 'r') as f:
                processed_data = json.load(f)

            channels = processed_data.get('channels', [])
            streams = processed_data.get('streams', [])
            ignore_tags = processed_data.get('ignore_tags', [])
            visible_channel_limit = processed_data.get('visible_channel_limit', 1)
            overwrite_streams = settings.get('overwrite_streams', True)
            if isinstance(overwrite_streams, str): overwrite_streams = overwrite_streams.lower() in ('true', 'yes', '1')

            ignore_quality = processed_data.get('ignore_quality', True)
            ignore_regional = processed_data.get('ignore_regional', True)
            ignore_geographic = processed_data.get('ignore_geographic', True)
            ignore_misc = processed_data.get('ignore_misc', True)

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

            processed_groups = 0
            total_groups = len(channel_groups)

            for group_key, group_channels in channel_groups.items():
                limiter.wait() # Rate limit processing
                sorted_channels = self._sort_channels_by_priority(group_channels)
                matched_streams, _, _, _, _ = self._match_streams_to_channel(
                    sorted_channels[0], streams, logger, ignore_tags, ignore_quality, ignore_regional, ignore_geographic, ignore_misc, channels_data
                )

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
                            if overwrite_streams:
                                ChannelStream.objects.filter(channel_id=channel_id).delete()

                            existing_stream_ids = set(ChannelStream.objects.filter(channel_id=channel_id).values_list('stream_id', flat=True))
                            streams_added = 0
                            for stream in matched_streams:
                                if not overwrite_streams and stream['id'] in existing_stream_ids: continue
                                ChannelStream.objects.create(channel_id=channel_id, stream_id=stream['id'])
                                streams_added += 1

                            channels_updated += 1
                            total_streams_added += streams_added
                        else:
                            if overwrite_streams:
                                ChannelStream.objects.filter(channel_id=channel_id).delete()
                    except Exception as e:
                        logger.error(f"[Stream-Mapparr] Failed to update channel '{channel['name']}': {e}")
                
                # Update progress
                processed_groups += 1
                progress = 20 + int((processed_groups / total_groups) * 60)  # 20-80%
                if processed_groups % max(1, total_groups // 10) == 0:  # Update every 10%
                    self._send_progress_update("add_streams_to_channels", 'running', progress, 
                                              f'Updated {channels_updated} channels so far...', context)

            # CSV Export - create if setting is enabled
            # Default to True if setting doesn't exist (matches field default)
            create_csv = settings.get('enable_scheduled_csv_export', True)
            if isinstance(create_csv, str):
                create_csv = create_csv.lower() in ('true', 'yes', '1')
            
            if create_csv:
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
                    current_threshold = settings.get('fuzzy_match_threshold', 85)
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

                    logger.info(f"[Stream-Mapparr] CSV export saved to {filepath}")
                except Exception as e:
                    logger.error(f"[Stream-Mapparr] Failed to create scheduled CSV export: {e}")

            self._trigger_frontend_refresh(settings, logger)

            # Send final completion notification
            success_msg = f"Updated {channels_updated} channels with {total_streams_added} streams."
            if channels_skipped > 0:
                success_msg += f" Skipped {channels_skipped} deleted channel(s)."
                logger.info(f"[Stream-Mapparr] {success_msg}")
            self._send_progress_update("add_streams_to_channels", 'success', 100, success_msg, context)

            return {"status": "success", "message": success_msg}

        except Exception as e:
            logger.error(f"[Stream-Mapparr] Error adding streams: {str(e)}")
            return {"status": "error", "message": f"Error adding streams: {str(e)}"}

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
                profile_id=profile_id,
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
                    profile_id=profile_id,
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

