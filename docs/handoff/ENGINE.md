> Part of the AI handoff docs — start at [`AGENTS.md`](../../AGENTS.md).
> Written 2026-11-08 against `main` @ `42c14dd`. Code is the source of truth: if this file and the
> code disagree, trust the code and fix this file. "UNSURE" = not verified by running it.

# Copy engine handoff: how a source post reaches destinations (`telkap/services/*`)


## 1. Two task modes

| | FULL (`Task.MODE_FULL="full"`) | SIMPLE (`Task.MODE_SIMPLE="simple"`) |
|---|---|---|
| Reader | The customer's own Telethon account (`userbot.py:UserbotManager`, `StringSession`, stored encrypted in `User.session_enc`) | A service-account pool (`pool.py`). Each source is leased to one account through `SourceLease` |
| Sender | The same customer account (`Copier._send`) | The aiogram bot itself (`Copier._send_by_bot` → `botsend.py`). The customer adds the bot as a channel admin, and `botsend.can_post` checks this when the task is created |
| How new posts arrive | Live Telethon events, with `sweeper.py` as a safety net | Polling only (`pubpoll.py:PublicPoller`, every 60 s). The service clients use `receive_updates=False` |
| Edits | `events.MessageEdited` → `Copier.sync_edit` | The poller re-checks posts that have `edit_date` set → `sync_edit` → `_edit_by_bot` |
| Deletes | `events.MessageDeleted` → `Copier._sync_delete` | **Not supported.** `_sync_delete` calls `manager.ensure_client`, and the poller has no delete signal |

`UserbotManager.reload_user` excludes SIMPLE tasks (`Task.mode != MODE_SIMPLE`) on purpose. If they were registered there, the same task would arrive by two paths, and the sweeper would label SIMPLE posts as `sweep`, which corrupts `timings.Report.sweep_share`.

Client selection lives in one place: `reader.for_task(task, manager)`. FULL returns `manager.ensure_client(user_id)`. SIMPLE returns `pool.lease(source_id)` → `pool.client_for`, or `pool.any_client()` when there is no `source_id` yet. Retry, release and the bot-mode downloader all use it. `reader.entity_for(client, source_id, source_ref)` first tries `get_input_entity(int)` and falls back to resolving the username. This exists because a `StringSession` stores the auth key but no entity cache, so a raw numeric ID fails with "Could not find the input entity" after every restart. This incident once took down all of SIMPLE mode.

## 2. FULL-mode runtime (`userbot.py`)

- **Login.** `start_login`/`submit_code`/`submit_password`, or QR (`start_qr_login`/`refresh_qr`/`qr_result`), then `_finish_login`. The client sets `parse_mode=None` because entities are always sent explicitly. The device fingerprint comes from the `DEVICE_MODEL`/`SYSTEM_VERSION`/`APP_VERSION` env vars. The proxy comes from `PROXY_URL`.
- **`UserRuntime`** holds `client`, `handlers`, and `source_map: chat_id → [task_id]`. `tasks_for_chat()` reads from it.
- **`reload_user`** removes the old handlers, resolves `source_id` (writing it back to the DB if it was missing), and registers `NewMessage`/`Album`/`MessageEdited`/`MessageDeleted` with `chats=chat_ids`. It then calls `_catch_up`, which runs `client.catch_up()` *after* the handlers exist (Telethon docs require this order) with a 45 s timeout. Before this was added, posts published during a restart were lost.
- **`ensure_client`** runs under a per-user lock. If authorization is lost, it records `health` and calls `logout(revoked=True)`.
- **`restore_all`** runs at startup with `Semaphore(5)` and a 90 s timeout per user. `main.py` then compares `listening_summary()` and alerts if `armed < enabled`.
- **`heal_forever`** runs every 300 s and re-runs `reload_user` for any user with enabled tasks but no connected runtime or an empty `source_map`.

## 3. How a new post flows (FULL)

1. **Handlers.** `Copier.make_new_message_handler` sends album messages (`grouped_id`) to `_buffer_album` and everything else to `_enqueue(Slot([msg], ready=True))`. `make_album_handler` fills the reserved slot with the authoritative `event.messages`.
2. **Album safety net.** `_slot_for` reserves a `Slot` in the queue *when the first album message arrives*, so a later text post cannot overtake the album. `_album_release` releases the slot with whatever has arrived after `ALBUM_SAFETY_SECONDS=4`. Reason: Telethon's album grouping is a 0.5 s "dirty hack" (its own comment), and grouped messages used to be dropped silently.
3. **Per-source ordered queue.** `_queues[(user, chat)]` has one `_worker` each. The worker waits up to `SLOT_WAIT_SECONDS=6`, sorts by message ID, and applies the order mode from `_order_for`. The strictest mode across all tasks on that source wins (`ORDER_RANK`):
   - `strict` awaits each dispatch.
   - `fast` spawns and moves on.
   - `grace` (default) waits up to `order_grace_seconds` (default 300, minimum 5), then lets the slow post continue in the background.

   `_spawn` keeps a reference to each running dispatch so it is not garbage-collected. Once a queue reaches `BUSY_QUEUE=20`, every further enqueue logs a warning and calls `alerts.send` (1800 s cooldown). Nothing is ever dropped.
4. **`_dispatch`** loops over `tasks_for_chat` and calls `process`. On `FloodWaitError` it records the error and health state, then sleeps `min(seconds, 300)`; this blocks that source's worker. For fatal health states (`health.FATAL_STATES`: peer_flood, banned, revoked) it calls `_handle_account_error` → `_pause_all_tasks` and stops.
5. **`process`** is an in-memory dedupe on `(task_id, first_msg_id)` (`_in_flight`). A duplicate concurrent call returns `False`. Queue workers call `in_flight()` *before* calling `process`, because `False` means "filtered → delete item" to them.
6. **Gates in `_process`, in order.** `admitted = retrying`, and retries skip every gate marked † to avoid burning quota or silently dropping the post.
   1. The snapshot is enabled (`cache.get_task`, in-memory, invalidated with `cache.invalidate_task`).
   2. † `within_active_hours`. Outside the window the post is either held (`hold_outside_hours` → `REASON_SCHEDULE`, `next_window_open`) or skipped.
   3. `cache.get_entitlement` (120 s TTL). No plan → `_pause_task` and a notification.
   4. `filters.should_copy` (media kind, forwarded, bot, reply, buttons, links, ads, size, length, block/allow rules). This runs on the **original** text.
   5. † Engagement (`min_views`/`min_reactions`/`min_forwards`). The post is first held for `engagement_wait_minutes`, then re-read and judged.
   6. † Approval (`cfg.approval` and not released from approval) → `_hold(REASON_APPROVAL)`, which notifies the user.
   7. † `min_gap_seconds` → `pending.next_slot` → hold as SCHEDULE.
   8. † `hourly_quota.allow` (in memory, `max_per_hour`).
   9. † `skip_duplicates` → `_seen_before(MessageMap.task_id==…)`, with modes exact, normalized or similar (`dedupe.py`: sha / normalized hash / simhash over the last 300 rows).
   10. † `_message_quota_ok`: the fair-use daily cap (`daily_quota`, seeded from `DailyStat`) plus `entitlement.reserve(FEAT_MESSAGES)`. The granted unit is **refunded** in a `finally` block unless `outcome.sent`, `already` or `queued` is true.
7. **`_deliver`.** FULL mode requires `ensure_client`; if there is none, the task is paused. Then:
   - `aipass.enhance` runs once for all destinations.
   - `apply_transforms` (rules → signature → config rewrite → removals → header/footer → `tidy`), `remap_entities` (UTF-16, `difflib`) and `with_own_entities` (the user's own formatted header/footer/signature).
   - The `delay_seconds` setting with ±15 % jitter.

   For each destination (`snapshot.targets[0]` is the primary; the others are `Destination` rows with `overrides`):
   - `only_target` filter.
   - On retry, the `_delivered` ID check.
   - Per-destination re-transform when it has overrides.
   - `routing.wants` (`route_skip` beats `route_words`).
   - † cross-destination dedupe (`skip_cross_duplicates`).
   - Watermark unit reservation (`FEAT_WATERMARK`, one per photo).
   - `limiter.acquire(target)` (`RATE_PER_MINUTE`, default 20, per destination).
   - `_send`.

   Error handling per destination:
   - `PremiumAccountRequiredError`: resend without custom emoji (`drop_custom_emoji`) and warn once.
   - `ChatWriteForbiddenError`: on the primary, pause the task; on an extra destination, call `_disable_destination`.
   - Fatal health error or `FloodWait`: re-raise.
   - `is_permanent` error: collect it into `unsendable`.
   - Error while `retrying`: collect into `failures`, then raise `SendFailed` at the end if nothing was delivered.
   - Any other error: `_enqueue_retry`.

   On success: `_remember` (writes `MessageMap` rows; the fingerprint goes only on the first album item; the table is trimmed to 3000 rows per task), `_bump`, and `_record_latency` (a `DeliveryTiming` row with `path` and `via`).

## 4. `_send` paths (`copier.py`)

| Situation | What happens |
|---|---|
| `simple` | `_send_by_bot`: text → `botsend.send_text`; poll → `Unsendable("simple-poll")`; opaque media → `Unsendable`; otherwise download with the service client (`_reader_for`) and `botsend.send_media`. An incomplete download raises `Unsendable`, never a partial post. |
| `cfg.mode=="forward"` | Plain `forward_messages`; the "forwarded from" label is kept. |
| Poll | `_send_poll` rebuilds an `InputMediaPoll`. A quiz without a visible correct answer raises `Unsendable("quiz-hidden-answer")`. |
| `caption_only` or text | `send_message(link_preview=False)` (link previews are always disabled). |
| Opaque media (`opaque_media`/`_OPAQUE_MEDIA`: Unsupported, PaidMedia, Story, Giveaway, Invoice, Game, GeoLive, Dice, or empty photo/document) | `_forward_opaque`: a raw `ForwardMessagesRequest(drop_author=True, drop_media_captions=…)` (`_forward_without_caption`), then `_set_caption`. An incomplete album is deleted and raises `PartialForward`. A caption that fails to apply is deleted and raises `CaptionNotSet` (transient) or `Unsendable`. If the source had no caption, a missing signature is tolerated. The `_NO_CAPTION_MEDIA` types skip the caption step. |
| `rewrite_files` on a document | `_rewritten_files` (`docedit.rewrite_file`, run in a thread), sent with `force_document`. Falls back to the next path if nothing changed or a file is missing. |
| Watermark | `_watermarked_files` → `send_file`. An incomplete set raises `Unsendable`. |
| Direct (default) | `send_file(m.media …)`. A single item is sent singly, because a 1-item list becomes an album and albums cannot carry buttons. On failure (for example a protected channel): `_download_all` → re-upload (`PATH_REUPLOAD`); if the download is incomplete → `_forward_opaque`. |

`_discard` never raises. A cleanup exception inside `finally` once turned successful sends into failures, and posts were duplicated up to 5 times. It accepts only a list or a single path.

## 5. Permanent vs transient errors

`is_permanent` returns true for `Unsendable`, `TypeError`, and the names in `_PERMANENT_ERRORS` (matched by name so other Telethon versions don't break imports). `ValueError` is deliberately **transient**, because "Could not find the input entity" goes away once the entity cache warms up.

`_give_up` writes `log_activity("unsendable")`, a daily `failed` count, and `abandoned.mark(REASON_UNSENDABLE)`. It notifies the customer at most once per `(task, kind)` per 24 h. The 31 October incident was one unsupported post: 86 retry items, about 430 sends, and 77 notifications.

## 6. Background workers (started in `telkap/main.py`)

- **`RetryWorker`** (`retry.py`, every 60 s, 50 items per run):
  - Re-reads the source through `reader`.
  - If the post was deleted at the source, drops the item.
  - If the post is in flight, calls `_postpone`.
  - Otherwise calls `process(retrying=True, via=retry, only_target=dest_chat)`.
  - `SendFailed`/exception → `_reschedule` with backoff `RETRY_BACKOFF=(60,300,900,3600)`. After `MAX_ATTEMPTS=4` it calls `abandoned.mark(EXHAUSTED)` and notifies.
  - `False` → drop the item (meaning a filter rejected it).
  - `retrying=True` matters: without it, each failure created a fresh item with 0 attempts, which looped forever (66 copies vs 2029 "failed" in one day).
  - `_enqueue_retry` pauses the task at `MAX_RETRY_QUEUE=60` pending items.
- **`ReleaseWorker`** (`pending.py`, every 60 s):
  - `due()` returns SCHEDULE items whose time has come.
  - `release()` deletes the row *before* calling `process(released=reason, via=queue)`. It waits up to 120 s if the post is in flight, otherwise re-holds it.
  - Approvals are released through `handlers/approvals.py` → `release_worker.release`.
  - `prune()` expires approvals after 72 h and marks them `EXPIRED`.
  - `reject`/`drop_task` mark posts `REJECTED` so the sweeper does not re-add them.
  - `_hold` treats "already waiting" as held. Before this, the sweeper published approval posts without approval after 3 minutes.
- **`Sweeper`** (`sweeper.py`, every 180 s):
  - For each listening chat, reads the last `LOOKBACK=30` posts above the lowest per-task watermark (`_watermarks` = max `MessageMap.src_msg_id`, computed **per task**).
  - Skips tasks that have never sent anything, so it never dumps a channel's archive.
  - Skips posts that are already sent, waiting for retry (`_waiting_retry`), held (`_held`) or abandoned.
  - Calls `process(via=sweep)`.
  - `watch_round` alerts when 5 consecutive rounds find posts (live updates are dead and the sweeper is carrying the service).
- **`PublicPoller`** (`pubpoll.py`, every 60 s, `LOOKBACK=25`):
  - Groups enabled SIMPLE Telegram tasks by `source_id` and leases an account.
  - Reads via `entity_for`, then runs `_sync_edits` on posts it has already seen.
  - On the first sighting it only sets `SourceLease.seen_msg_id` and sends nothing.
  - `seen_msg_id` is advanced *after* the send attempts.
  - `FloodWait` → `pool.mark_flood`; a dead session → `pool.mark_banned`, which releases its leases.
  - No account available → `alerts.send("pool-empty")`.
- **`heal_forever`** (see section 2).
- **`maintenance.run_forever`** (every 6 h) prunes the activity log, `timings` (60 d) and `abandoned` (7 d).
- **`HistoryCopier`** (`history.py`) runs on demand, one job per user. It is FULL-only (uses `manager.ensure_client`), reads oldest→newest, groups albums, and calls `process(via=history)`.
- RSS tasks (`Task.SOURCE_RSS`) are handled by `feedworker.py` and are outside this map.

## 7. Edit and delete sync

`sync_edit` requires `sync_edits` and existing `MessageMap` rows. It compares `fingerprint_of(...).exact` against the stored `content_hash`, not `edit_date`, because view-count changes also mark a post as edited. It calls `_restamp` (updates all 3 hash levels) **only if every edit succeeded**, so transient failures are retried in a later round.

`_sync_delete` is FULL-only, requires `sync_deletes`, and deletes per destination.

## 8. Account health (`health.py`)

`classify` maps Telethon errors to `ok`, `flood`, `peer_flood`, `banned`, `revoked` or `error`. `record` notifies the user only when the state changes and the state is fatal. `clear` runs after a successful connect.

## 9. Known limitations and risks

- **Telethon layer 193** (pinned `Telethon==1.38.1`). Newer post types arrive as `MessageMediaUnsupported` and can only be copied by forwarding (FULL). SIMPLE mode marks them `Unsendable`.
- **`StringSession` keeps no entity cache.** Any raw-ID call needs `reader.entity_for`. UNSURE: `Sweeper._sweep` calls `client.iter_messages(chat_id)` with a raw int on FULL clients, which could fail after a restart if the entity was never seen.
- **Big public channels / non-member sources send no live updates.** The sweeper (FULL) and the poller (SIMPLE) compensate, so latency is roughly half the polling interval.
- **Bot API custom emoji limits.** Bots can send premium emoji only with a Fragment collectible username, and the "premium owner" path does not apply to channels. The `custom_emoji` mapping in `botsend.to_bot_entities` is kept on purpose. In FULL mode, premium emoji needs the customer's account to have Premium.
- **SIMPLE gaps:**
  - No polls, no opaque media, no delete sync.
  - All media goes through a full download and re-upload (`file_id` is bot-scoped).
  - `botsend.trim` cuts by Python `len`, not UTF-16 length, and entities are not clipped (UNSURE whether this can cause `EntityBoundsInvalid`).
  - `pool.client_for` passes no `proxy=` and no device fingerprint.
- **`pool.revive` has no callers.** A service account marked `STATE_FLOOD` never returns to `_usable()` on its own (UNSURE whether admin handlers reset it some other way).
- **`Copier._last_path` is shared across concurrent sends,** and the text, poll and forward-mode branches of `_send` never set it. The path labels in `DeliveryTiming` can therefore be stale.
- **`sync_edit` does not apply per-destination `overrides`, `with_own_entities` or `aipass`.** Edited destinations get the task-level transform only.
- **Likely bug (untested): approval combined with `min_gap_seconds`.** After approval the post is re-held as SCHEDULE. When that releases with `released="schedule"`, the approval gate fires again.
- **`hourly_quota` and `daily_quota` are not refunded** when a post is not delivered (only the `FEAT_MESSAGES` entitlement unit is).
- **All in-memory state is lost on restart:** queues, `_in_flight`, the rate limiter and the warn-once sets.

Tests covering this engine (`tests/`): `test_copier.py`, `test_retry_loop.py`, `test_sweeper.py`, `test_pubpoll.py`, `test_simple_mode.py`, `test_unsendable*.py`, `test_reader.py`, `test_pool.py`, `test_botsend.py`, `test_timings.py`, `test_premium_emoji.py`, `test_entities.py`, `test_v3.py`–`test_v16.py`.