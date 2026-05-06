# Code Review — Action Items for Fix

## P0 — Bugs (fix first)

### 1. `app/state_store.py:56-66` — Data-loss race in `save_async`
**Problem:** Snapshot of `BotState` is taken BEFORE acquiring `_save_lock`. Two concurrent callers (e.g. workers in `_finalize_added_torrents_batch`) can interleave: A snapshots stale state, B snapshots and saves, then A saves and overwrites B's changes.
**Fix:** Move `snapshot = BotState(...)` inside `async with self._save_lock:`.

### 2. `app/torrent_handlers.py:152-154` — Dead code
**Problem:** `qbit.resolve_torrent()` raises `ValueError` when no match, never returns `None`. The `if not torrent:` after line 152 is unreachable.
**Fix:** Remove the `if not torrent:` block (lines 152-154).

### 3. `app/link_handlers.py:98-105` — Orphaned background tasks on shutdown
**Problem:** `application.create_task(...)` in `_start_add_background_tasks` returns a Task that is never stored. `post_shutdown` only cancels `completion_monitor_task` and `watchdog_task` — the add-finalizer tasks leak.
**Fix:** Store the returned Task in `application.bot_data` (e.g. `"add_finalizer_tasks"`) and cancel/await them in `post_shutdown`.

### 4. `app/state_store.py:109` — Crash on corrupt legacy JSON
**Problem:** `json.loads()` in `_migrate_legacy_json` has no try/except. Corrupt file crashes startup.
**Fix:** Wrap in try/except `json.JSONDecodeError`, log warning, and skip migration.

---

## P1 — Medium

### 5. `app/jobs.py:165-166` — Partial file priority state on error
**Problem:** `set_file_priority` called twice (large files, small files). If second call fails, small files stay at priority 1. No rollback.
**Fix:** Either batch both priority changes into one call (if qBittorrent API supports it) or at least catch and log the second failure explicitly.

### 6. `app/state_store.py:222` vs `:258` — Off-by-one on expiry boundary
**Problem:** Memory expiry check is `expires_at > now`, DB cleanup is `expires_at <= now`. When `expires_at == now`, entry lives in memory but gets deleted from DB on next save.
**Fix:** Make them consistent — use `>=` in memory or use `<` in DB, whichever is intended.

### 7. `app/jobs.py:265-268` — Silent failure when file selection not ready
**Problem:** `_apply_jav_file_selection` returns `NOT_READY` — torrent is categorized but files aren't filtered, and no user notification fires.
**Fix:** Add a specific check for `NOT_READY` result and send a message like "JAV categorized but file metadata not ready yet — retry with /retryjav".

### 8. `app/handler_utils.py:37-41` — `_resolve_hash_or_reply` lets network errors through
**Problem:** Only catches `ValueError`. `qbit.resolve_hash()` makes HTTP calls — `httpx.HTTPError`, timeouts propagate uncaught to caller (e.g. `pause_handler`).
**Fix:** Add `except Exception` fallback that calls `_reply_qbit_action_error(update, exc)`.

---

## P2 — Low / Cleanup

### 9. `app/formatters.py:200-201` — Dead function
**Problem:** `filter_name_to_view()` is never imported or called anywhere.
**Fix:** Delete it.

### 10. `app/formatters.py:13-53` — Duplicate dicts
**Problem:** `_STATE_LABELS` and `_STATE_ICONS` define the same 16 keys. Drift risk.
**Fix:** Merge into one dict, e.g. `_STATE_INFO = {"downloading": ("⬇️", "⬇️ 下载中"), ...}`.

### 11. `app/torrent_handlers.py:180-266` — Repeated handler pattern
**Problem:** `pause_handler`, `resume_handler`, `delete_handler`, `delete_files_handler` share ~90% identical structure.
**Fix:** Extract to `_torrent_action_handler(action_fn, success_label)`.

### 12. `app/jobs.py:179` — Full torrent list every 30s
**Problem:** `_notify_completion_loop` fetches ALL torrents to detect completions. Heavy on large instances.
**Fix:** Use `filter="completed"` or `filter="all"` with `hashes` param for only active torrents.

### 13. `app/add_links.py:171` — Full torrent list for dedup on every add
**Problem:** `_add_torrent_links` fetches all torrents just to build `known_hashes`.
**Fix:** Cache the last fetch for a short duration, or accept a small dedup window.

### 14. `app/add_links.py:82` — Torrent link detection false positives
**Problem:** `/download` substring matches non-torrent URLs like `.../not-download/asset.mp4`.
**Fix:** Tighten patterns (e.g. require `.torrent` extension or specific path boundaries).
