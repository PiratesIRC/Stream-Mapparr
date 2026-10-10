"""Usage counter client for Dispatcharr plugins.

This file is copied byte-identically into each plugin package and pinned by sha256.
Edit it only in plugin-stats/client/, then re-copy it with client/vendor.py.

It sends one plugin's cumulative work total and a random id for this plugin on this
install to the plugin-stats Worker, at most once an hour, and only while the
plugin's "share_usage_counts" setting allows it. It never raises into the plugin.
"""
import errno
import glob
import http.client
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import fcntl
except ImportError:  # Windows: no flock, so reporting is disabled there.
    fcntl = None

ENDPOINT = "https://plugin-stats.dpas.workers.dev"
SCHEMA = 1
DATA_DIR = "/data/plugin_stats"
SEND_INTERVAL = 3600
FORCE_INTERVAL = 600
BACKOFF_FAILURE = 900
BACKOFF_RATE_LIMITED = 3600
DISABLED_FOR = 30 * 86400
SOCKET_TIMEOUT = 10
TOTAL_TIMEOUT = 20
YIELD_EVERY = 500
MAX_LEDGER_BYTES = 64 * 1024 * 1024
MAX_TOTAL = 1000000000
SETTING_ID = "share_usage_counts"
SETTING_LABEL = "Share anonymous usage counts"
USER_AGENT = "plugin-stats-client/1"

_warned = set()


def _default_logger():
    import logging
    return logging.getLogger("plugin_stats")


def _warn_once(logger, code, message):
    """Log message once per process for each code. Never raises."""
    if code in _warned:
        return
    _warned.add(code)
    try:
        (logger or _default_logger()).warning(message)
    except Exception:
        pass


def _errno_name(exc):
    return errno.errorcode.get(getattr(exc, "errno", None) or 0, type(exc).__name__)


def _valid_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def jsonl_sum(pattern, key=None, where=None, fields=None, logger=None):
    """Total over every JSONL file matching pattern (rotated files included).

    key: sum this integer field. fields: sum these integer fields. Neither: count
    the lines. where: only lines whose fields equal these values. Malformed lines,
    booleans, negatives and non-integers are skipped. A file over MAX_LEDGER_BYTES
    is skipped whole with one warning. The reader yields every YIELD_EVERY lines so
    a large ledger cannot hold a gevent worker.
    """
    total = 0
    names = [key] if key is not None else (list(fields) if fields is not None else None)
    for path in sorted(glob.glob(pattern)):
        base = os.path.basename(path)
        subtotal = 0
        try:
            if os.path.getsize(path) > MAX_LEDGER_BYTES:
                _warn_once(logger, "big:" + path, "usage ledger over the size cap was skipped: " + base)
                continue
            with open(path, encoding="utf-8", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    if number % YIELD_EVERY == 0:
                        time.sleep(0)
                    try:
                        record = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    if not isinstance(record, dict):
                        continue
                    if where and any(record.get(k) != v for k, v in where.items()):
                        continue
                    if names is None:
                        subtotal += 1
                        continue
                    for name in names:
                        value = record.get(name)
                        if _valid_int(value):
                            subtotal += value
        except OSError as exc:
            _warn_once(logger, "read:" + path, "usage ledger unreadable (" + _errno_name(exc) + "): " + base)
            continue
        total += subtotal
    return total


def json_field(path, field, logger=None):
    """One non-negative integer field of a small JSON counter file, else 0."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, RecursionError) as exc:
        _warn_once(logger, "json:" + path,
                   "usage counter file unreadable (" + _errno_name(exc) + "): " + os.path.basename(path))
        return 0
    value = data.get(field) if isinstance(data, dict) else None
    return value if _valid_int(value) else 0


_HEX = frozenset("0123456789abcdef")


def _valid_id(value):
    return isinstance(value, str) and len(value) == 32 and set(value) <= _HEX


def _read_text(path):
    """The stripped text of path; None when it does not exist, "" when it exists but
    cannot be read as ASCII, so the caller treats it as corrupt and replaces it."""
    try:
        with open(path, encoding="ascii") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return ""


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _write_temp(directory, text):
    temp = os.path.join(directory, ".tmp-%d-%s" % (os.getpid(), uuid.uuid4().hex))
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        handle = os.fdopen(fd, "w", encoding="ascii")
    except BaseException:
        os.close(fd)
        _discard(temp)
        raise
    try:
        with handle:
            handle.write(text)
    except BaseException:
        _discard(temp)
        raise
    return temp


def load_install_id(directory):
    """The random id for this plugin on this install, creating it if needed.

    Created with os.link from a fully written temp file, which is atomic and fails
    when the name exists, so creators racing end with the winner's id and a reader
    never sees a half-written file. A corrupt file is removed and re-created the
    same way. Returns None when no valid id can be obtained. Never derived from
    hardware or network values.

    Callers must hold the plugin's lock file (UsageReporter does), because the repair of
    a corrupt file is a remove followed by a link and is not atomic between two repairers.
    """
    path = os.path.join(directory, "install_id")
    current = _read_text(path)
    if _valid_id(current):
        return current
    if current is not None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            return None
    try:
        temp = _write_temp(directory, uuid.uuid4().hex)
    except OSError:
        return None
    try:
        os.link(temp, path)
    except FileExistsError:
        pass
    except OSError:
        return None
    finally:
        try:
            os.remove(temp)
        except OSError:
            pass
    final = _read_text(path)
    return final if _valid_id(final) else None


_OPT_IN_STRINGS = ("true", "True", "1", "yes", "on")


def _opted_in(value):
    """True only for an explicit true value. Type checks are exact, so 1.0 and any
    other value that merely compares equal to 1 is an opt-out."""
    if value is True:
        return True
    if type(value) is int and value == 1:
        return True
    return type(value) is str and value in _OPT_IN_STRINGS


def _touch(path, mtime):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(fd)
    os.utime(path, (mtime, mtime))


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _remove(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _in_future(path, now):
    until = _mtime(path)
    return until is not None and until > now


def _blocked(directory, now, min_gap=SEND_INTERVAL):
    """True while a report must not be sent: sent less than min_gap seconds ago, or a
    backoff or disabled file whose mtime is still in the future. A forced report passes
    the shorter FORCE_INTERVAL gap; backoff and disabled checks always apply."""
    sent = _mtime(os.path.join(directory, "sent"))
    if sent is not None and now - sent < min_gap:
        return True
    return _in_future(os.path.join(directory, "backoff"), now) or \
        _in_future(os.path.join(directory, "disabled"), now)


def _gap(force):
    return FORCE_INTERVAL if force else SEND_INTERVAL


def _flock(fd):
    """Non-blocking exclusive lock; False when another process holds it."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _start_thread(fn):
    threading.Thread(target=fn, name="plugin-stats-report", daemon=True).start()


def _close_db_connection():
    """A background thread that read settings through the ORM opened its own
    database connection; close it so each hourly send does not leak one."""
    try:
        from django.db import connection
        connection.close()
    except Exception:
        pass


class UsageReporter:
    """Reports one plugin's cumulative total. Call report(settings) with the live
    settings dict at the end of run() and after the plugin writes its ledger; it
    returns at once and never raises."""

    def __init__(self, plugin, counter, label, total_fn, settings_fn=None, data_dir=DATA_DIR,
                 endpoint=ENDPOINT, http=None, start=None, lock=None, clock=time.time):
        self.plugin = plugin
        self.counter = counter
        self.label = label
        self.total_fn = total_fn
        self.settings_fn = settings_fn
        self.endpoint = endpoint.rstrip("/")
        self.data_dir = data_dir
        self.directory = os.path.join(data_dir, plugin)
        self._http = http
        self._start = start or _start_thread
        self._lock = lock
        self._clock = clock

    def report(self, settings=None, logger=None, force=False):
        """Send if due. force is for the call made right after the plugin records new
        work: it allows a send ten minutes after the last one instead of an hour, so a
        fresh total is not held back for long by an earlier report."""
        try:
            self._report(settings, logger, force)
        except Exception as exc:
            _warn_once(logger, "report:" + self.plugin,
                       "usage report skipped for " + self.plugin + " (" + _errno_name(exc) + ")")
        return None

    def _consent(self, settings):
        """True to send, False when opted out, None when no settings are available."""
        if settings is None and self.settings_fn is not None:
            settings = self.settings_fn()
        if not isinstance(settings, dict):
            return None
        if SETTING_ID not in settings:
            return True
        return _opted_in(settings[SETTING_ID])

    def _report(self, settings, logger, force=False):
        lock = self._lock or (_flock if fcntl is not None else None)
        if lock is None:
            return
        consent = self._consent(settings)
        if consent is None:
            return
        if consent:
            if _blocked(self.directory, self._clock(), min_gap=_gap(force)):
                return
            action = self._send_report
        else:
            if not os.path.exists(os.path.join(self.directory, "install_id")):
                return
            if _in_future(os.path.join(self.directory, "delete_backoff"), self._clock()):
                return
            action = self._send_delete
        os.makedirs(self.data_dir, mode=0o700, exist_ok=True)
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        fd = os.open(os.path.join(self.directory, "lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            if not lock(fd):
                os.close(fd)
                return
            if consent and _blocked(self.directory, self._clock(), min_gap=_gap(force)):
                os.close(fd)
                return
            self._start(lambda: self._run(fd, action, logger))
        except BaseException:
            os.close(fd)
            raise

    def _run(self, fd, action, logger):
        """Background work. The lock is released only by closing fd, here."""
        try:
            action(logger)
        except Exception as exc:
            _warn_once(logger, "send:" + self.plugin,
                       "usage send failed for " + self.plugin + " (" + _errno_name(exc) + ")")
            name = "delete_backoff" if action == self._send_delete else "backoff"
            try:
                self._set_until(name, BACKOFF_FAILURE)
            except OSError:
                pass
        finally:
            _close_db_connection()
            os.close(fd)

    def _set_until(self, name, seconds):
        _touch(os.path.join(self.directory, name), self._clock() + seconds)

    def _call(self, method, path, body):
        http = self._http or _http_request
        return http(method, self.endpoint + path, body)

    def _send_report(self, logger=None):
        if self.settings_fn is not None and self._consent(None) is not True:
            return
        install = load_install_id(self.directory)
        if install is None:
            _warn_once(logger, "install:" + self.plugin,
                       "usage install id unavailable for " + self.plugin)
            self._set_until("backoff", BACKOFF_FAILURE)
            return
        total = self.total_fn()
        if not _valid_int(total):
            return
        if total > MAX_TOTAL:
            _warn_once(logger, "max:" + self.plugin,
                       "usage total above the server limit for " + self.plugin)
            return
        status = self._call("POST", "/v1/report", {
            "schema": SCHEMA, "install": install, "plugin": self.plugin,
            "counter": self.counter, "total": total,
        })
        if status == 202:
            _touch(os.path.join(self.directory, "sent"), self._clock())
            _remove(os.path.join(self.directory, "backoff"))
        elif status == 410:
            self._set_until("disabled", DISABLED_FOR)
        elif status == 429:
            self._set_until("backoff", BACKOFF_RATE_LIMITED)
        else:
            self._set_until("backoff", BACKOFF_FAILURE)

    def _send_delete(self, logger=None):
        """Opted out: ask the server to delete this install's figures, then forget the
        id. A failure keeps the id and sets delete_backoff, which only this path reads,
        so a report's backoff or a 410 never holds a delete back."""
        install = _read_text(os.path.join(self.directory, "install_id"))
        if _valid_id(install):
            status = self._call("DELETE", "/v1/install", {
                "schema": SCHEMA, "install": install, "plugin": self.plugin,
            })
            if status not in (204, 404):
                wait = BACKOFF_RATE_LIMITED if status == 429 else BACKOFF_FAILURE
                self._set_until("delete_backoff", wait)
                return
        for name in ("install_id", "sent", "backoff", "disabled", "delete_backoff"):
            _remove(os.path.join(self.directory, name))


def _http_request(method, url, body):
    """Send body as compact JSON; return the HTTP status, or None on any transport
    failure. Under gevent the whole call is bounded by TOTAL_TIMEOUT, because the
    socket timeout applies per operation and a server that drips bytes could hold
    the call far longer."""
    data = json.dumps(body, separators=(",", ":")).encode("ascii")
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "User-Agent": USER_AGENT,
    })

    def call():
        try:
            with urllib.request.urlopen(request, timeout=SOCKET_TIMEOUT) as response:
                return response.status
        except urllib.error.HTTPError as exc:
            return exc.code
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
            return None

    try:
        import gevent
    except ImportError:
        return call()
    with gevent.Timeout(TOTAL_TIMEOUT, False):
        return call()
    return None


def load_plugin_settings(key, logger=None):
    """The stored settings dict of one plugin, read fresh from Dispatcharr's
    PluginConfig, or None when unavailable (logged once)."""
    try:
        from apps.plugins.models import PluginConfig
        value = PluginConfig.objects.filter(key=key).values_list("settings", flat=True).first()
    except Exception as exc:
        _warn_once(logger, "settings:" + key,
                   "usage settings lookup failed for " + key + " (" + type(exc).__name__ + ")")
        return None
    return value if isinstance(value, dict) else None


def with_usage_field(fields, reporter):
    """fields plus the opt-out checkbox, whose help text names what is sent and where."""
    fields = list(fields)
    if any(isinstance(f, dict) and f.get("id") == SETTING_ID for f in fields):
        return fields
    host = urllib.parse.urlsplit(reporter.endpoint).netloc
    help_text = (
        "Sends this plugin's " + reporter.label + " total and a random id for this plugin on "
        "this install to the plugin author's counter at " + host + " when the plugin runs, at "
        "most once an hour, or ten minutes after the last report when the plugin has just recorded "
        "new work, so the README badges count every install that runs at least one action. The "
        "server stores that id with the total and the date of the last report. The connection "
        "shows the server your public IP address; the server uses it only to limit abuse and does "
        "not store it in its database, though when an install first registers it keeps a salted "
        "one-way hash of it (of its /64 block for IPv6) for up to three days. Cloudflare, which "
        "hosts the server, keeps its own request logs. No names, channels, streams, providers or "
        "settings are sent. Untick to stop sending; this install's figures are deleted from the "
        "server the next time the plugin runs after you untick."
    )
    fields.append({"id": SETTING_ID, "label": SETTING_LABEL, "type": "boolean",
                   "default": True, "help_text": help_text})
    return fields
