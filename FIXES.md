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
