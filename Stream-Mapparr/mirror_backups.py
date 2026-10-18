"""Plan the mirror streams that give each channel a backup on every provider host.

M3U accounts in one Dispatcharr server group can be mirrors of each other: the
same provider stream ids served from different provider hosts. Measured
2026-10-18 on one installation, three accounts held 5,924 streams each and
every provider stream id appeared on all three, while only 5,031 of the names
matched exactly between two of them. So a mirror is found by provider stream
id, never by name.

For each channel, the streams it already holds from an account in a server
group name the provider stream ids to look for. For each other ACTIVE account
in that group whose host the channel has no stream from yet, the mirror stream
is added. One mirror per missing host: two accounts on the same host would add
a second copy of the same failure.

Pure: no ORM, no Django. The caller loads the rows and writes the result.
Nothing is ever removed from a channel.
"""
from urllib.parse import urlsplit


def host_key(server_url):
    """`host` or `host:port`, lower case, or None when the URL names no host.

    Credentials in the URL are dropped. The caller must not log or report this
    value: on a real installation the host is the provider's name.
    """
    if not server_url:
        return None
    try:
        parts = urlsplit(server_url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except (ValueError, TypeError, AttributeError):
        return None
    if not host:
        return None
    return f"{host}:{port}" if port else host


def _insert_position(streams, accounts):
    """Index just after the last stream whose account has a provider host.

    Trailing streams with no host, such as a custom placeholder stream, stay
    last, where the operator placed them.
    """
    position = 0
    for i, stream in enumerate(streams):
        account = accounts.get(stream.get("account_id"))
        if account and account.get("host"):
            position = i + 1
    return position


def plan_mirror_fill(channel_streams, accounts, mirror_index, tick=None, stats=None):
    """Return one entry per channel that gains at least one mirror stream.

    channel_streams: {channel_id: [{"id", "account_id", "provider_id"}, ...]}
        in the channel's current order.
    accounts: {account_id: {"name", "group_id", "active", "host"}}.
    mirror_index: {(account_id, provider_id): stream_id} for the accounts in
        server groups.
    tick: called once per channel, so a caller under gevent can yield.
    stats: when a dict is passed, it is filled with how many channels fell
        into each outcome: "gained" (in the plan), "covered" (already holds a
        stream from every active host in its group), "no_source" (holds no
        stream from a server-group account with a provider stream id, so there
        is nothing to look a mirror up by), "no_mirror_found" (a host is
        missing but no account on it carries the mirror).

    Each entry: {"channel_id", "old_order", "new_order", "added"}, where
    "added" lists {"stream_id", "account_id", "source_stream_id"}. Entries are
    ordered by channel id.
    """
    group_members = {}
    for account_id, account in sorted(accounts.items()):
        group_id = account.get("group_id")
        if group_id is not None:
            group_members.setdefault(group_id, []).append(account_id)

    counts = {"gained": 0, "covered": 0, "no_source": 0, "no_mirror_found": 0}
    plan = []
    for channel_id in sorted(channel_streams):
        if tick is not None:
            tick()
        streams = channel_streams[channel_id]
        held = {s["id"] for s in streams}
        hosts = set()
        for s in streams:
            account = accounts.get(s.get("account_id"))
            if account and account.get("active") and account.get("host"):
                hosts.add(account["host"])

        added = []
        source_groups = set()
        for source in streams:
            provider_id = source.get("provider_id")
            source_account = accounts.get(source.get("account_id"))
            if provider_id is None or not source_account:
                continue
            group_id = source_account.get("group_id")
            if group_id is None:
                continue
            source_groups.add(group_id)
            for candidate_id in group_members.get(group_id, ()):
                if candidate_id == source.get("account_id"):
                    continue
                candidate = accounts[candidate_id]
                host = candidate.get("host")
                if not candidate.get("active") or not host or host in hosts:
                    continue
                mirror_id = mirror_index.get((candidate_id, provider_id))
                if mirror_id is None or mirror_id in held:
                    continue
                added.append({"stream_id": mirror_id, "account_id": candidate_id,
                              "source_stream_id": source["id"]})
                held.add(mirror_id)
                hosts.add(host)

        if not added:
            if not source_groups:
                counts["no_source"] += 1
            elif any(accounts[a].get("active") and accounts[a].get("host")
                     and accounts[a]["host"] not in hosts
                     for g in source_groups for a in group_members.get(g, ())):
                counts["no_mirror_found"] += 1
            else:
                counts["covered"] += 1
            continue
        counts["gained"] += 1
        old_order = [s["id"] for s in streams]
        at = _insert_position(streams, accounts)
        new_order = old_order[:at] + [a["stream_id"] for a in added] + old_order[at:]
        plan.append({"channel_id": channel_id, "old_order": old_order,
                     "new_order": new_order, "added": added})
    if stats is not None:
        stats.update(counts)
    return plan
