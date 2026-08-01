"""Stream-Mapparr's emit layer for the Newsflasharr notification service.

This module owns the guard boundary. The vendored client's notify() never
raises, but the code around it can, and a bug here must never break
Stream-Mapparr's real work. Every public function in this file is written so a
failure is reported rather than thrown.

Settings are read from the dict passed in on every call and never cached on an
instance. A value primed on one entry path and read back with getattr on another
fails silently, with no crash and no log line, which is a failure this codebase
has shipped before.
"""
import json
import os

# Written by the SCHEDULED path only. Newsflasharr's own absence detector stamps
# a timestamp on ANY successful attachment send, so it cannot tell a scheduled
# run from a button press. This file is the only signal that can answer whether
# the schedule itself is still alive.
SCHEDULED_RUN_FILE = "/data/stream_mapparr_scheduled_run.json"

_TRIGGERS = ("never", "scheduled", "every_run")
_DEFAULT_TRIGGER = "scheduled"

# The plugin key. Newsflasharr routing and deduplication both key on it, so it
# must stay stable.
SOURCE = "stream-mapparr"

# The event name every report notification carries. Newsflasharr routing rules
# match on it, so it must stay stable. "usage_report" matches the naming the
# other report callers on this installation already use (dustarr and
# metricsarr both emit usage_report), so an operator reading the routing rules
# sees one convention rather than three.
EVENT = "usage_report"

# Everything Newsflasharr needs configured before it can send mail at all. Only
# the PRESENCE of each is ever checked or reported: smtp_password is one of
# them and its value must never reach a log line or a toast.
SMTP_REQUIRED = ("smtp_server", "smtp_username", "smtp_password", "smtp_to")


def routes_to_smtp(nf_settings, source=SOURCE, event=EVENT):
    """Would a report from this plugin actually reach the email channel?

    Newsflasharr sends an event to `default_channels` when no rule matches it,
    so a missing routing rule is invisible from this side: the spool write
    succeeds, a delivery is recorded, and the mail goes somewhere other than
    the inbox. This is the check that makes that visible.

    `routing_rules` is stored as a JSON string, not a list, so it is parsed
    defensively; a list is accepted too in case that ever changes. A rule with
    no source or no event is a wildcard and matches. Never raises.
    """
    nf_settings = nf_settings if isinstance(nf_settings, dict) else {}
    raw = nf_settings.get("routing_rules")
    rules = raw if isinstance(raw, list) else []
    if isinstance(raw, str):
        try:
            rules = json.loads(raw)
        except (ValueError, TypeError):
            rules = []
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        match = rule.get("match") if isinstance(rule.get("match"), dict) else {}
        if match.get("source") not in (None, source):
            continue
        if match.get("event") not in (None, event):
            continue
        if any("smtp" in str(c).lower() for c in (rule.get("channels") or [])):
            return True
    return "smtp" in str(nf_settings.get("default_channels") or "").lower()


def is_enabled(settings):
    """Is the Newsflasharr master toggle on?

    Public on purpose. The Email Report Now button needs this check to fail fast
    before it does any work, and a caller in another module must not reach for a
    private helper: nothing would pin that call, so an ordinary rename in this
    file would break the button silently.
    """
    value = (settings or {}).get("notify_enabled", False)
    if isinstance(value, str):
        value = value.lower() in ("true", "yes", "1")
    return bool(value)


def resolve_report_trigger(settings):
    """Return "never", "scheduled" or "every_run", never anything else.

    An unrecognised or missing value resolves to "scheduled" rather than raising
    or guessing. dict.get cannot distinguish absent from present-but-None, and
    Dispatcharr never prunes a stored setting when its field is removed.
    """
    value = (settings or {}).get("notify_report_on", _DEFAULT_TRIGGER)
    if not isinstance(value, str):
        return _DEFAULT_TRIGGER
    value = value.strip().lower()
    return value if value in _TRIGGERS else _DEFAULT_TRIGGER


def should_emit(settings, is_scheduled):
    """Return (bool, reason). The reason is operator-readable when False."""
    if not is_enabled(settings):
        return False, "notifications to Newsflasharr are switched off"
    trigger = resolve_report_trigger(settings)
    if trigger == "never":
        return False, "the report trigger is set to never"
    if trigger == "scheduled" and not is_scheduled:
        return False, "the report trigger is set to scheduled runs only"
    return True, None


def emit_reports(notify_fn, settings, written, *, is_scheduled):
    """Emit one notification per report file. Returns {"sent", "skipped_reason"}.

    `notify_fn` is injected rather than imported so tests can observe the call
    without a spool directory.

    Each path must be a caller-owned, never-rewritten timestamped file that
    already exists on disk. An email send re-reads the attachment path on every
    retry attempt, so a file rewritten in place would be a different file on the
    second attempt. A path that is missing is skipped rather than sent, because
    a green task result does not prove an artifact was published.

    Two events are emitted, not one: a notification carries a single attachment,
    so the HTML page and the CSV arrive as two separate emails.
    """
    result = {"sent": 0, "skipped_reason": None}
    try:
        allowed, reason = should_emit(settings, is_scheduled)
        if not allowed:
            result["skipped_reason"] = reason
            return result
        written = written or {}
        if written.get("error"):
            result["skipped_reason"] = written["error"]
            return result
        for key, label in (("html_path", "HTML report"), ("csv_path", "CSV report")):
            path = written.get(key)
            if not path or not os.path.isfile(path):
                continue
            ok = notify_fn(
                source=SOURCE,
                title=f"Stream-Mapparr {label} ready",
                body=f"Attached: {os.path.basename(path)}",
                event=EVENT,
                severity="info",
                kind="event",
                dedup_key=None,
                url=None,
                attachment=path,
            )
            if ok:
                result["sent"] += 1
    except Exception as e:
        result["skipped_reason"] = f"the emit path raised and was contained: {e}"
    return result


def write_scheduled_run_ts(path, now):
    """Record that the SCHEDULED path completed a run.

    Written by the scheduled path only, never by an action or a button. That
    restriction is the whole point: Newsflasharr's absence detector stamps a
    timestamp on any successful attachment send and cannot tell a scheduled run
    from a button press, so an operator pressing the button occasionally would
    keep it fresh forever and a dead schedule would never surface.

    Never raises. A health signal must not break the run it reports on.
    """
    tmp = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"last_scheduled_run_ts": float(now)}, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def read_scheduled_run_ts(path):
    """Return the recorded timestamp, or None when it cannot be established.

    Absent, unreadable and corrupt all read as None, meaning "never ran". That
    is the safe direction: an unreadable health signal must not be mistaken for
    a healthy one.
    """
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f).get("last_scheduled_run_ts")
        return float(value) if value is not None else None
    except Exception:
        return None
