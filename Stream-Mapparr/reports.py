"""Report model and rendering for Stream-Mapparr.

The model is built by COPYING a known set of keys rather than by filtering an
unknown one. Newsflasharr sends an attachment verbatim and unredacted, so
anything reaching this model can leave the box by email, and a column added to
an export later must not be able to start being emailed on its own.

Two measured facts about this installation drive the design:

1. Every CSV export in /data/exports appends the M3U account name to each stream
   name, and on this box those account names are the provider's hostnames.
2. 327 stream names contain square brackets and not one of them holds an account
   name. The brackets hold the market, which for an over-the-air station is its
   whole identity.

So the primary defence is that build_model is fed RAW stream names, which never
carried an account label, and sanitise_stream_label removes an EXACT account
name only, as a backstop.
"""
import re

# Matches a bare IPv4 address. The Sort export carries provider edge server
# addresses in a column of its own; this catches one reaching a name field by
# any other route.
_IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")


def sanitise_stream_label(label, account_names):
    """Remove an M3U account name in brackets, and nothing else.

    An unknown bracketed value is left alone, because on this installation those
    hold the market rather than a source label. Removing every bracketed group
    would strip nothing that leaks and would collapse dozens of distinct
    over-the-air stations into one indistinguishable name.

    Account names are matched longest first: "streamq.tv" is a prefix of
    "streamq.tv-bk15", and matching the shorter one first would leave a
    "-bk15]" fragment behind.
    """
    text = str(label or "")
    for account in sorted([a for a in (account_names or []) if a], key=len, reverse=True):
        text = re.sub(r"\s*\[" + re.escape(account) + r"\]", "", text)
    return text.strip()


def _scrub(value, account_names):
    """Apply every content rule to one free-text value."""
    cleaned = sanitise_stream_label(value, account_names)
    return _IPV4_RE.sub("", cleaned).strip()


def build_model(channels, account_names, settings, now):
    """Build the report model from a list of per-channel result dicts.

    Only the keys named below are copied. Anything else the caller passes is
    dropped rather than carried through.

    `stream_names` must be a list. A caller that passes the semicolon-joined
    string the CSV export builds would otherwise have it iterated one character
    at a time, producing a report of single letters, so a string raises instead
    of being silently mangled.
    """
    entries = []
    for row in channels or []:
        raw = row.get("stream_names") or []
        if isinstance(raw, str):
            raise TypeError(
                "stream_names must be a list, not the joined string the CSV "
                "export builds. Pass the raw matched stream names.")
        names = [_scrub(s, account_names) for s in raw]
        entries.append({
            "channel_name": _scrub(row.get("channel_name"), account_names),
            "matched": int(row.get("matched") or len(names)),
            "stream_names": names,
        })
    return {
        "generated_ts": float(now or 0),
        "channel_count": len(entries),
        "entries": entries,
    }
