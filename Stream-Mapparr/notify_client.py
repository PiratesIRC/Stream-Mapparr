"""Notifyarr caller client — vendor this single file into your plugin.

Writes one redacted JSON event into Notifyarr's spool. Stdlib only, no
imports from Notifyarr. never raises. True = durably spooled (NOT
"delivered"). Strings you pass here are POSTed to external services
(Discord etc.): never place raw stream URLs, provider hostnames or
unfiltered exception text in title/body — redaction is best-effort
shape-matching, not a guarantee. Contract: notifier spec §3.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import types

SCHEMA_V = 1
CLIENT_VERSION = "1.2.0"
DEFAULT_BASE = "/data/newsflasharr"
MAX_BODY_BYTES = 65536
MAX_SPOOL_FILES = 1000
# Headroom only `critical` may consume, so a backed-up spool cannot
# silently swallow a real incident behind a flood of info events.
CRITICAL_RESERVE = 200
COUNT_CACHE_S = 5.0
_STATE_KEY = "_notifyarr_client_state"

_CREDS_RE = re.compile(r"(/(?:live|movie|series)/)[^/\s?]+/[^/\s?]+(?=[/?\s]|$)",
                       re.IGNORECASE)
_QUERY_CREDS_RE = re.compile(r"([?&](?:username|user|password|pass)=)[^&\s]+",
                             re.IGNORECASE)
_BASIC_AUTH_RE = re.compile(r"(://)[^/\s@]+:[^/\s@]+(@)")
_SOURCE_RE = re.compile(r"[^a-z0-9_-]")

# Stripping the username and password out of a provider URL leaves the edge
# hostname and the numeric stream id, and both identify the operator's provider
# account to anyone reading a Discord message or an email. Those are removed too.
#
# The rewrite is scoped to URLs, never to free text, and that scoping is the
# whole safety argument. A rule that hunts for bare hostnames in prose needs a
# curated top-level-domain allowlist, a file-extension denylist, a two-label
# minimum and public-suffix handling, because .ts, .sh, .zip and .mov are all
# real top-level domains. A host sitting between "://" and the next "/" needs
# none of that, because the URL syntax has already identified it.
_URL_RE = re.compile(
    r"(?P<scheme>[a-z][a-z0-9+.\-]*)://(?P<authority>[^/\s?#]*)"
    r"(?P<path>[^\s?#]*)(?P<query>[?#][^\s]*)?",
    re.IGNORECASE)

# A URL is treated as media, and therefore rewritten, on either signal: a
# provider path segment, or a streaming file extension. Anything else is left
# alone, because destroying a link to documentation or to an issue makes
# notifications less useful while protecting nothing.
_MEDIA_PATH_RE = re.compile(r"/(?:live|movie|series)/", re.IGNORECASE)
_MEDIA_EXT_RE = re.compile(
    r"\.(?:ts|m3u8|m3u|mp4|mkv|avi|flv|m4v|mov|mpd|mpg|mpeg|wmv)$",
    re.IGNORECASE)


def _redact_media_url(match):
    """Rewrite one URL if it looks like media, otherwise return it unchanged.

    The scheme, the /live/ style path segments and the file extension survive,
    so a reader can still tell what kind of value was removed. The authority,
    which carries any host, port and remaining userinfo, and the final path
    segment, which carries the stream id, do not.
    """
    whole = match.group(0)
    path = match.group("path") or ""
    if not (_MEDIA_PATH_RE.search(path) or _MEDIA_EXT_RE.search(path)):
        return whole

    segments = path.split("/")
    last = segments[-1]
    ext = ""
    dot = last.rfind(".")
    if dot > 0:
        ext = last[dot:]
    segments[-1] = "<redacted>" + ext
    # The query string on a media URL is where a session token lives, so it is
    # dropped rather than kept. On a non-media URL it is left untouched above.
    return f'{match.group("scheme")}://<redacted>{"/".join(segments)}'


def redact(text):
    if text is None:
        return None
    out = str(text)
    out = _CREDS_RE.sub(r"\1<redacted>/<redacted>", out)
    out = _QUERY_CREDS_RE.sub(r"\1<redacted>", out)
    out = _BASIC_AUTH_RE.sub(r"\1<redacted>:<redacted>\2", out)
    return _URL_RE.sub(_redact_media_url, out)


def _client_state():
    """Per-process mutable state parked in sys.modules — module globals are
    wiped by Dispatcharr's reload ping-pong (bug-136)."""
    mod = sys.modules.get(_STATE_KEY)
    if mod is None:
        mod = types.ModuleType(_STATE_KEY)     # no __file__: loader ignores it
        mod.cache = {}
        sys.modules[_STATE_KEY] = mod
    return mod.cache


def _spool_has_room(spool_dir, now, severity="info"):
    """Backpressure, with a reserve that only `critical` may enter.

    This guard used to be CALLER-BLIND: past MAX_SPOOL_FILES it returned
    False for everything, so a critical incident arriving while the spool
    was backed up was dropped with no ledger row, no failed/ file and no
    trace of any kind. The spool only fills when the collector has fallen
    behind, which is exactly when a critical matters most.

    Non-critical events are refused at MAX_SPOOL_FILES; critical may use a
    further CRITICAL_RESERVE on top. A critical refused even then leaves a
    `spool_full` marker (see notify()), which Newsflasharr's `show_status`
    reports as an error naming the age of the marker.

    That last clause used to read "so the loss is at least visible" while NO
    production code opened the file -- a docstring asserting a safety property
    the code did not implement, which is worse than plain silence because it
    stops the next reader checking.
    """
    cache = _client_state()
    hit = cache.get(spool_dir)
    if hit and now - hit[1] < COUNT_CACHE_S:
        count = hit[0]
    else:
        try:
            count = sum(1 for f in os.listdir(spool_dir) if f.endswith(".json"))
        except OSError:
            count = 0
        cache[spool_dir] = (count, now)
    limit = MAX_SPOOL_FILES
    if str(severity) == "critical":
        limit += CRITICAL_RESERVE
    return count < limit


def _mark_spool_full(spool_dir, severity, now):
    """Leave a trace when a CRITICAL is refused.

    Best-effort and deliberately silent on failure: this runs on the
    caller's hot path and must never raise. A single marker file is
    rewritten rather than appended, so a sustained flood cannot itself
    fill the disk.
    """
    if str(severity) != "critical":
        return
    try:
        path = os.path.join(os.path.dirname(spool_dir), "spool_full")
        with open(path, "w", encoding="utf-8") as f:
            f.write(str(int(now)))
    except Exception:
        pass


def _truncate_utf8(text, limit):
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore")


def notify(source, title, *, event=None, body="", severity="info",
           kind="event", dedup_key=None, url=None, attachment=None,
           base_dir=DEFAULT_BASE, _now_ms=None):
    try:
        spool_dir = os.path.join(base_dir, "spool")
        os.makedirs(spool_dir, exist_ok=True)
        now = time.time()
        if not _spool_has_room(spool_dir, now, severity):
            _mark_spool_full(spool_dir, severity, now)
            return False
        ts = int(_now_ms) if _now_ms is not None else int(now * 1000)
        payload = {"v": SCHEMA_V, "client_v": CLIENT_VERSION,
                   "source": str(source), "kind": str(kind),
                   "severity": str(severity), "ts": ts,
                   "title": _truncate_utf8(redact(str(title)) or "", 1024),
                   "body": _truncate_utf8(redact(str(body)) or "", MAX_BODY_BYTES)}
        if event:
            payload["event"] = str(event)
        if dedup_key:
            payload["dedup_key"] = str(dedup_key)
        if url:
            payload["url"] = redact(str(url))
        if attachment:
            payload["attachment"] = str(attachment)
        cache = _client_state()
        counter = cache["counter"] = (cache.get("counter", 0) + 1) % 10000
        safe = _SOURCE_RE.sub("_", str(source).lower())[:32] or "unknown"
        rand8 = os.urandom(4).hex()
        name = f"{ts}-{counter:04d}-{safe}-{rand8}.json"
        tmp = os.path.join(spool_dir, f".tmp-{name}")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        os.replace(tmp, os.path.join(spool_dir, name))
        return True
    except Exception:
        return False


def notifier_alive(base_dir=DEFAULT_BASE, max_age_s=120.0):
    try:
        return time.time() - os.path.getmtime(
            os.path.join(base_dir, "state.json")) < max_age_s
    except Exception:
        return False
