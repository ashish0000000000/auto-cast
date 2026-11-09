# AutoCast — Bug Fixes

## 1. CRITICAL — "❌ Channel not found in your dialogs" (even when admin)
**Cause:** peer resolution read `access_hash` off pyrofork's high-level `Chat`
object (`getattr(chat, "access_hash", 0)` / `getattr(dialog.chat, ...)`), but
that object has no `access_hash` attribute — it exists only on the raw layer.
So `warm_peer_and_get_hash()` always returned `0`, which made:
  - **Add channel by ID** always fail with "Channel not found in your dialogs".
  - **Scheduled posts** auto-pause with a bogus "you are no longer an admin" notice.
  - **Auto-delete / pin** silently give up ("could not resolve chat").
Membership/admin status was never actually tested — the code just failed to read
a non-existent field.

**Fix:** get the real `access_hash` from `client.resolve_peer()` (returns a raw
`InputPeerChannel` that carries it) after warming the peer via `get_chat()` or a
`get_dialogs()` scan. New helpers `_resolve_access_hash()` / `_cache_access_hash()`;
`warm_peer_and_get_hash()` rewritten; the import resolver (3-tier) updated the same way.

## 2. Poll scheduling crashed
**Cause (send):** pyrofork's `send_poll` expects `options` as `PollOption` objects
(it reads `option.text`), but the bot passed plain strings → `AttributeError`.
**Cause (capture):** a poll message has no caption/text, so `content_text` was
stored as `None`; the send path then did `json.loads(None)`.
**Fix:** capture polls to JSON (`_capture_content_text()`), and convert options to
`PollOption` at send time (with an empty-caption guard).

All changes verified: `py_compile` clean, module imports, and the resolution
control flow unit-tested (fast-path hit, dialog-scan hit, genuine non-member → 0).

---

# Round 3 — reliability, links, security (Nov 2026)

## ⚠️ Before you deploy
- **`ENCRYPTION_KEY` is now required.** The bot exits on startup without it.
  Set it to a long random string and **never change it**
  (`python -c "import secrets;print(secrets.token_urlsafe(48))"`). Existing
  plaintext sessions are encrypted automatically on first start. If you already
  had a key set, keep the same one. (To opt out: `ALLOW_PLAINTEXT_SESSIONS=1`.)
- DB changes are automatic: new column `userbot_tasks_v11.claimed_until`, new table `userbot_meta`.
- Optional env vars: `JOB_CONCURRENCY` (8), `OWNER_CONCURRENCY` (2), `JOB_TIMEOUT` (900 s),
  `CATCHUP_MAX_MIN` (60), `MISSED_ONCE_GRACE_MIN` (60), `DB_POOL_MAX` (20),
  `BROADCAST_CHANNEL_GAP` (5 s), `USER_CLIENT_IDLE_SECS` (900), `IMPORT_MAX_MB` (50),
  `ADMIN_IDS` (comma-separated Telegram IDs allowed to see `/proxy` details).

## A. Scheduled posts being skipped
1. **DB pool deadlock.** Each firing held a pooled connection for an advisory lock for
   the whole send, while the send needed a second one. About 10 posts due at once (a
   10-channel broadcast) took every connection, so everything froze until the watchdog
   restarted the bot. → Replaced with one atomic `claim_task()` query; pool raised to 20;
   global and per-account concurrency limits. *Test: the old pattern deadlocks; the new
   one completes 30 simultaneous posts on a 5-connection pool.*
2. **Posts due during a restart or redeploy were dropped.** APScheduler's `IntervalTrigger`
   always jumps to the *next* slot after a reload. → New `LocalIntervalTrigger` with
   catch-up: a missed repeat is sent on startup if it is at most 60 min late (and no
   more than half its interval). One-time posts missed for longer are **paused and the
   owner is notified**, instead of disappearing.
3. **Hung connections blocked a task forever** (`max_instances=1`). → Every post now has
   a hard timeout (`JOB_TIMEOUT`).
4. **Silent failures.** Missing-permission, unreachable-channel, too-long and lost-media
   errors now pause the task and tell the owner why. Temporary errors are retried, and
   the owner is told after 3 failures in a row. One-time posts retry 3 times.
   A dead or revoked login stops the engine and notifies the owner once. (Before, a
   missing session **deleted** the user's tasks.)
5. **Self-healing sweep** (every 5 min) re-registers any task that lost its scheduler job.
6. **Duplicate posts:** the claim advances the next-run time in the same statement, so a
   second instance during a zero-downtime deploy, or a crash, can't post the same slot twice.
7. **Broadcast burst:** channels are now staggered by `BROADCAST_CHANNEL_GAP` seconds
   instead of all posting in the same second.
8. **DST:** daily and weekly repeats keep the same local time in the owner's timezone
   (no change for IST or UTC).

## B. Link-only posts never sent
A message that is just a link arrives with media `WEB_PAGE_PREVIEW`. It was stored as
type `web_page_preview`, which the sender had no case for, so nothing was sent and
nothing was logged. → Captured as `link` and sent as text **with the preview shown**.
Tasks already saved with the old type now send too. Video notes are now supported;
other unsupported types (dice, location, contact…) are rejected when you send them,
with a message.

## C. Security
- **Task ID collisions:** IDs were `task_<unix-seconds>_…`, so two users scheduling in the
  same second overwrote each other's task content (posted from the other user's account).
  → Random IDs (`new_task_id()`).
- **No ownership checks:** button data can be forged, so any user could view, edit,
  pause or delete anyone's tasks. → `get_owned_task(tid, uid)` on every task action;
  channel actions check `_owns_channel()`.
- Sessions must be encrypted (see top). `/proxy` details are admin-only.
- Login: wrong code or wrong 2FA password now really retry (the old flow dropped the
  state). Code and password messages are deleted from the chat. Abandoned logins
  time out and disconnect. `/start` cancels a half-finished login.

## D. Other fixes
- **Dialog-scan fallback was dead code:** pyrofork 2.3.69's `get_dialogs()` has no
  `folder_id` and raised `TypeError`. Round 1's fix #1 only worked on its fast path.
  → `get_dialogs()` without arguments (covers the archive too).
- **One shared, long-lived Telegram connection per account** (`user_clients`) instead of
  a new login for every post, pin, delete and preview. A pin no longer opens a second connection.
- **Each button press is answered exactly once.** The second `q.answer()` raised
  `QUERY_ID_INVALID` and aborted the handler: "timezone set" then showed "Invalid
  timezone", and screens didn't refresh after delete or update.
- **Repost matcher** no longer adopts other people's posts: media must be the same file
  (`file_unique_id`), short texts must match exactly, and channel posts by your own
  account are recognised via `outgoing`.
- **Delete previous** now runs *after* the new post is live. `last_msg_id` is saved right
  after sending, so reply chains reply to the current message.
- **Engine stop/start** no longer resets per-task pause state. Starting the engine only
  resumes what you didn't pause yourself. (One-time migration on first start.)
- **Export/import:** total embedded media is capped so a backup always fits the import
  limit. Paused state is kept. Re-importing the same backup no longer duplicates tasks.
- Content source fallback no longer points at an unrelated message; queue buttons are bounds-checked.

## Verified
`py_compile` and pyflakes clean. Against pyrofork 2.3.69, APScheduler 3.10.4 and a real
PostgreSQL 16: 39 logic/DB/scheduler tests, 7 login-flow tests and 4 startup-migration
tests, all passing. Not tested against live Telegram — watch the logs after deploying.

---

# Round 4 — Coolify healthcheck + reconnect crash

## `RuntimeError: read() called while another coroutine is already waiting for incoming data`
Pyrogram's `Session.restart()` has no guard. When the bot's connection drops, the ping
task, the network task and the packet handler can each start a restart at the same
moment. The restarts run in parallel and two receive loops read the same socket. The
connection then flaps or hangs until the watchdog restarts the container. → `main.py`
now allows only one restart per session at a time. You'll see
`Telegram connection dropped — reconnecting session` once instead of the traceback.

## "Running (no healthcheck)" in Coolify
New: a heartbeat file (`/tmp/autocast.health`) plus `healthcheck.py`. The check passes
only after the DB is up, Telegram is connected and every task is loaded. It keeps
passing only while the watchdog's Telegram + DB check succeeds (default: within 3 min).
It doesn't need curl/wget in the image or an exposed port.

**Set it up in Coolify:** your application → **Healthcheck** → enable →
- Check type: **CMD**
- Command: `python healthcheck.py`
- Interval `30` · Timeout `10` · Retries `3` · Start period `120`

Then **Redeploy**. The status should change to **Running (healthy)**. On later deploys
Coolify waits for the new container to be healthy before removing the old one; a short
overlap is safe because each post is claimed in the DB.

Optional: set `HEALTH_PORT=3000` (or any port) to also serve HTTP `/health`
(200 / 503 with JSON) if you prefer an HTTP-type check.

---

# Round 5 — "Delete previous" stopped working (regression from round 3)

**Cause:** these channels have a "Safe Mode"/search-proofing bot that deletes each new
post and reposts it with altered text (homoglyphs, invisible chars, an added footer) and
often a re-uploaded file. Round 3 made the repost matcher strict (same file and nearly
the same text), so the repost no longer matched. AutoCast kept tracking its own original,
which the other bot had already deleted. On the next cycle it "deleted" that missing
message and the visible repost stayed.

**Fix**
- Content matching is lenient again: fuzzy text (≥80% similar, or one contains the other),
  matching captions, or the same file. Same media type with no caption to compare also counts.
- Safety now comes from *timing*: a post only counts as our repost if our post vanished and
  it appeared within `REPOST_WINDOW_SECS` (default 180 s) of ours. Messages tracked by
  other AutoCast tasks in the channel are never adopted or deleted.
- Watching for the repost now lasts 35 s (checks at 5/7/10/20/35 s) instead of 10 s.
- Safety net: if the previous message is already gone when it's time to delete it, AutoCast
  searches *between* that message and the new post for a repost of the same content and
  deletes it. This catches repost bots slower than 35 s. A caption-less photo with a
  different file never counts in this scan.
- No more silent failures: "nothing deleted" is logged, and if the account lacks the
  *Delete messages of others* admin right the owner gets a one-time message saying so.

Verified with a simulated Safe-Mode bot over 4 cycles (fast and slow reposts, an
unrelated post, another task's post): the previous version fails 5 of the checks,
the new one passes 10/10. All earlier suites still pass.

## Round 5b — Auto-delete after X minutes
- It had the same regression: when the repost bot replaced a post, AutoCast found no live
  message and **never scheduled the auto-delete**. The matcher fix above restores it, and
  the auto-delete now targets the repost.
- New safety net for slow repost bots (slower than the 35 s watch): when the auto-delete
  finds the original already gone, it deletes the matching repost. Only posts made around
  that post's send time, with the same content, count. Other tasks' posts and unrelated
  posts are never touched. (Auto-deletes reloaded after a restart don't use this fallback.)
