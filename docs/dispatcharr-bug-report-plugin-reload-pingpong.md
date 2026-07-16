# Dispatcharr bug report — perpetual cross-worker plugin force-reload ("reload-token ping-pong")

**Status:** draft, not yet filed upstream.
**Dispatcharr version observed:** 0.27.2 (also reproduced on a second, independent installation).
**Severity:** High — degrades the web UI to nginx 504 over time on affected installs; any plugin
that starts a background resource from `__init__` leaks one instance per HTTP streaming event.

## Summary

Once any single force reload of the plugin registry happens (e.g. installing or updating a
plugin), installations running more than one process that dispatches connect events — i.e. any
normal uWSGI multi-worker setup — enter a **permanent force-reload loop**: every connect-event
dispatch re-imports every enabled plugin module from scratch, forever. The loop never converges
because reacting to a stale reload token *itself touches the token*, re-staling every other
process.

## Mechanism

Three pieces interact (`apps/plugins/loader.py`, `apps/connect/utils.py`):

1. **Event dispatch runs discovery on the request path.** `apps/connect/utils.py` calls
   `PluginManager.get().discover_plugins(sync_db=False, use_cache=True)` on **every**
   `client_connect` / `client_disconnect` / `channel_start` / `channel_stop` event — i.e. on
   every proxied-stream start/stop, inside the gevent worker handling the request.

2. **The cache is bypassed whenever the reload token is newer than the process's last-seen
   value.** In `discover_plugins()`:

   ```python
   token = self._get_reload_token()          # mtime of /data/plugins/.reload_token
   if use_cache and not force_reload:
       ...return cached registry only if token <= self._last_reload_token...
   if token > self._last_reload_token:
       force_reload = True                   # stale token -> full force reload
   if force_reload:
       self._touch_reload_token()            # <-- bumps the mtime AGAIN
       token = self._get_reload_token()
   ```

3. **The ping-pong.** `PluginManager` is a per-process singleton; `_last_reload_token` is
   per-process state. Consider workers A and B (uWSGI runs several, plus Celery):

   - Something legitimately touches the token once (plugin install/update/reload).
   - A's next event dispatch: token > A.last → force reload → **touch** (mtime T1) → A.last = T1.
   - B's next event dispatch: T1 > B.last → force reload → **touch** (T2) → B.last = T2.
   - A's next event dispatch: T2 > T1 → force reload → **touch** (T3) → …

   With ≥2 processes alternating discoveries, every discovery is stale and re-stales everyone
   else. The loop is self-sustaining and never converges. Each force reload purges every
   enabled plugin's modules from `sys.modules` (`_unload_package` / `_unload_alias` /
   `_unload_path_modules`) and re-execs them.

## Consequences

- **Full plugin re-import per streaming event, inside the request path.** Module `exec` of every
  enabled plugin (plus its imports) is CPU-bound Python running in the gevent worker — it blocks
  all 400 async cores of that worker for its duration, on every channel zap.
- **Any resource a plugin arms from `__init__`/import is leaked once per event.** Module-level
  state is wiped by the re-import, so idempotence guards keyed on module globals never engage,
  and cleanup handles (e.g. a `threading.Event` a background thread listens to) are stranded in
  the discarded module namespace. Concretely: plugins that start a background scheduler thread
  on construction (Stream-Mapparr, Event-Channel-Managarr — both mirror this common pattern)
  leak **one un-stoppable thread per channel zap** on affected installs (~6/min observed while a
  user zapped channels). Under gevent these are greenlets that accumulate without bound until
  the worker degrades and nginx starts returning 504 — presenting to the user as "Dispatcharr
  crashes when zapping channels; container restart fixes it temporarily".

## Evidence

**Install 1 (user report, 0.27.2, 4 uWSGI workers).** During ordinary channel zapping, the log
shows the full cycle once per zap, on every worker PID (211–214):

```
15:50:33,131 INFO apps.plugins.loader Discovered 2 plugin(s)
15:50:33,113 INFO plugins.stream_mapparr [Stream-Mapparr] Stream-Mapparr Plugin v1.26.1960020 initialized
15:50:33,115 INFO plugins.stream_mapparr [Stream-Mapparr] Background scheduler started for times: ['05:30']
... (repeats at 15:50:25, :33, :35, :39, :40, :43, :53, :55, :56, 15:51:03, :09, :52, :55, :57, 15:52:47 — ~15 full
    re-imports in a 2.5-minute log; "Background scheduler started" on each = one leaked thread each)
```

**Install 2 (our own, 0.27.2, independent config).** Verified live on 2026-07-16:

- `/data/plugins/.reload_token` mtime read `06:26:16` when the host clock read `06:26:24` — the
  token is being touched continuously with **no** plugin management activity at all.
- `event_channel_managarr` (schedule configured) logs `Background scheduler started` on every
  `Discovered 8 plugin(s)` line — one leaked scheduler thread per discovery. A sibling plugin
  with no schedule configured shows the matching `initialized` lines at the same timestamps,
  confirming whole-module re-imports rather than mere re-instantiation.

## Reproduction

1. Run Dispatcharr with the default multi-worker uWSGI config and ≥1 enabled plugin.
2. Trigger one force reload (install/update any plugin, or `touch /data/plugins/.reload_token`).
3. Start/stop proxied streams from two clients (or zap channels) so that connect events land on
   different workers.
4. Observe `Discovered N plugin(s)` and per-plugin `initialized` log lines on every event,
   indefinitely, and watch the `.reload_token` mtime advance continuously.

## Suggested fixes (independent; any one breaks the loop)

1. **Don't re-touch the token when reacting to it.** `_touch_reload_token()` should be called
   only when the *caller* explicitly requested `force_reload=True` (an actual "please reload
   everywhere" signal) — never on the `token > _last_reload_token` reaction path. Reacting is
   consuming the signal, not raising it; the current code turns every consumer into a producer.
2. **Make event dispatch cache-only.** `iter_actions_for_event` reads the in-memory registry;
   the `discover_plugins(use_cache=True)` call preceding it could tolerate a stale token (or be
   rate-limited) rather than force-reloading inside a streaming request.
3. **Reload off the request path.** If a stale token must trigger a reload, defer it to a
   background task instead of performing module purge + re-exec inside the gevent request
   handler.

## Plugin-side mitigation (what we shipped meanwhile)

Stream-Mapparr ≥ 1.26.196xxxx parks its scheduler state (thread handle, stop event, arm
signature, lock) in a synthetic `sys.modules` entry whose name matches none of the loader's
purge patterns and which carries no `__file__`, so a re-import finds the live thread and
re-arming stays a no-op. That removes the thread leak for this one plugin but cannot remove the
per-event re-import cost, and every other plugin with `__init__`-armed resources remains
exposed.
