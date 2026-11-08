> Part of the AI handoff docs — start at [`AGENTS.md`](../../AGENTS.md).
> Written 2026-11-08 against `main` @ `42c14dd`. Code is the source of truth: if this file and the
> code disagree, trust the code and fix this file. "UNSURE" = not verified by running it.

# Project history and decisions (handoff section)

## 0. Repository and branch state
- **`main`** is the production branch (the server pulls it via `deploy/update.sh`). Tip on 2026-11-08: `42c14dd`, one squashed commit holding the whole "unsendable posts / abandoned posts / approval bypass" fix. `main`'s root is `2a912d5` (2026-09-03).
- **`claude/telkap-bot-clone-5ct7ru`** is an older AI dev branch with a different root (`a90b917`, 2026-08-02). Its tree equalled `main` on 2026-11-08. Branch new work from `main`.
- Test suite on 2026-11-08: 1209 passed, ruff + pyflakes clean.

## 1. Product purpose and business context

- «فورواردبات» is a Persian Telegram content-copy SaaS, modelled on the Iranian service telkap.ir (`a90b917`). It copies posts from source channels to destination channels and applies text rules, filters, signature or footer, watermark, edit/delete sync and dedupe.
- The owner runs their own Telegram channels that earn money from ads. They built the bot so they would not have to pay a channel admin, and they sell it to others as a subscription. Plans, wallet, ZarinPal/Zibal/TRON payments, resellers ("نماینده", `54b8b6f`), a sales site (`site/`, `447cf30`, `5a1e7c7`) and a Mini App (`2a912d5`, `d6df6e8`, `bd31f66`).
- Two modes:
  - **Full mode.** Uses the customer's own Telegram account through Telethon (QR or code login). Supports private sources and Premium emoji.
  - **Simple mode** (`Task.mode`, built 2026-09-14). No customer login. The project's own service accounts read public sources without joining (`telkap/services/pool.py`, `pubpoll.py`), and the bot itself posts (`telkap/services/botsend.py`).

## 2. How the owner works with you

- **Persian only, non-technical.** On 2026-10-31 the owner replied to an English report: «فارسی بگو نمیفهمم چی میگی». All user-facing replies must be in plain Persian. Code comments and commit messages in this repo are also Persian.
- **Server access.** The server runs Linux. The owner connects with `ssh root@<server>` from Windows PowerShell, where copy-paste works. The hosting panel's VNC console is the fallback, and it cannot display Persian. That is why every diagnostic tool prints ASCII and takes `--send`, which delivers the report as a file to Telegram: `tools/why.py`, `review.py`, `stuck.py`, `simplecheck.py`.
- **Deploy command:** `sudo bash /opt/telkap/deploy/update.sh`. An earlier assistant once gave the wrong path, `/opt/telkap/update.sh`, which fails with "command not found".
  - What `update.sh` does (`03b0aad`): it compiles the new code in a temp directory before stopping the service, runs pip only if the requirements changed, and takes a DB backup after the stop, deliberately. It prints the downtime.
  - The app lives in `/opt/telkap`, runs as user `telkap`, and the service is `telkap.service`.
- **Report command:** `cd /opt/telkap && sudo -u telkap /opt/telkap/.venv/bin/python tools/review.py --send`. The exact argument form (for example a days argument) is UNSURE.
- The owner expects the assistant to tell them when to run `update.sh`. Don't ask them to deploy work that is still under review.

## 3. Hard rules and constraints

1. **Never ask the owner to paste secrets in chat.** This covers `BOT_TOKEN`, `API_ID`, `API_HASH`, `FERNET_KEY`, `AI_API_KEY`, `TRON_API_KEY`, `ZARINPAL_MERCHANT` and the Zibal merchant code, the root password, the panel password, and SSH private keys. Tell the owner to put them directly into the server `.env` or into the bot. A wallet private key or seed phrase must never enter the project.
2. **No refunds.** Business rule: the service only receives money and never pays customers. Never promise a refund. Enforced by `tests/test_terms.py::test_the_text_never_promises_a_refund`.
3. **Never copy `.env.example` over `.env`.** It wipes `FERNET_KEY`, and every stored session becomes undecryptable. Enforced by `tests/test_deploy.py`, which scans for `cp .env.example .env`. `tools/envsync.py` exists for merging new keys.
4. **Never log into the owner's Telegram** through third-party connectors such as Chiho or an "Unofficial Telegram MCP". That account owns their income channels.
5. **Two programs must never use the same Telegram session at once** (risk of AUTH_KEY_DUPLICATED). `tools/stuck.py` refuses to run while `systemctl is-active telkap` reports the service as active.
6. **Login QR warning.** The UI must tell users never to forward the login QR image, because whoever scans it connects *their* account (`10c4160`). The QR login text must not claim "read-only" access; a test guards this (`547aeac`).
7. **Git:** don't force-push. Don't push unreviewed work to `main`. Ship one clean commit to `main` after review and tests.
8. **The repo is public.** Never commit secrets or personal data. If a credential is ever exposed, tell the owner and recommend rotating it; the owner decides.
9. **Product honesty.** Don't promise features the platform can't deliver, for example Premium emoji in simple mode (`0a29a9b`; tests in the same family as `test_terms`).
10. **Mirroring to Iranian messengers.** If Bale/Eitaa/Rubika support is built, never mirror the owner's own channels there without asking first (some content is not appropriate for those platforms).

## 4. Major incidents and fixes, in order

| Date | Commit | What happened | Root cause and fix |
|---|---|---|---|
| 2026-08-02 | `a90b917`, `9f18859` | Initial build | aiogram 3 handles the UI; Telethon handles MTProto reading. Sessions are encrypted with Fernet (`telkap/crypto.py`). |
| 2026-09-03 to 09-06 | `2a912d5`, `d6df6e8`, `bd31f66` | Mini App built | `telkap/web/`; nginx routes the JSON API to the bot (`b309e60`). |
| 2026-09-06 | `2874e28` | Tasks died after every restart | Three causes: the client had no `catch_up` (now called *after* handlers are registered); `restore_all` ran serially with no timeout (now parallel, max 5, with a timeout); no self-heal (`manager.heal()` now runs every 5 min). |
| 2026-09-06 | `f7d5325` | **September retry-loop incident.** One user's daily summary showed 66 copied vs 2029 failed. | `copier.process` created a *new* `RetryItem` with attempts=0 on every failure, and the worker then dropped the old item, so `MAX_ATTEMPTS` was never reached. The minute-by-minute retries triggered account-wide FloodWait, which stalled healthy tasks too. Fix: `process(retrying=True)` raises `SendFailed`; each task's retry queue has a cap (`MAX_RETRY_QUEUE`) that pauses the task; admin alert. Added `tools/why.py`. Task 5 still shows `failed_total≈9691` from this incident. |
| 2026-09-07 | `d497b05` | Live updates silently stopped arriving | Telethon issue #4345: big public channels the account doesn't own send no updates. Added `telkap/services/sweeper.py:Sweeper`, which polls every source every `SWEEP_INTERVAL=180`s (`LOOKBACK=30`). The watermark is kept per task (`_watermarks`). Tasks that never sent anything are skipped. Since then about 92% of deliveries come through the sweeper. |
| 2026-09-13 | `547aeac` | Users refused to hand a login code to a bot | Added QR login (refreshes every 25s; 2FA continues through a button). |
| 2026-09-14 | `914e1e8`, `74dcfe9`, `c497669`, `702b191`, `e7f0089`, `2fab225`, `6420dc3` | **Simple mode built** | An experiment showed public channels can be read without joining. Added `ServiceAccount`/`SourceLease` pool, the `Task.mode` field (default full), and a seam at send time in the copier (bot sends instead of the account). Added the `pubpoll` scanner; its first visit only sets the watermark. Media path added. |
| 2026-09-15 | `670e062` | "پست کامل نیومد" (post arrived incomplete) | Simple mode had no edit sync. Added `Copier.sync_edit` and `pubpoll._sync_edits`. |
| 2026-09-15 | `4e2a6d1` | **StringSession entity-cache bug.** Simple mode worked only until the first restart. | StringSession stores only the auth key, not `access_hash`, so after a restart every call failed with "Could not find the input entity". Fix: `telkap/services/reader.py:entity_for`, which tries the cache first and falls back to resolving the username. Used in `pubpoll._poll`, `retry._attempt` and `pending.release`. |
| 2026-09-15 | `0a29a9b`, `43fbe5a` | **Premium emoji decision** | Bot API custom emoji work only for bots that own a Fragment collectible username. The "owner has Premium" route does not cover channels. Owner's decision: keep simple mode simple, and tell users that Premium emoji, private sources and similar need full mode (QR or code). Built `MODE_COMPARISON` in `telkap/handlers/simple_task.py` with callbacks `task:mode:up` and `task:mode:down`. The `custom_emoji` branch in `botsend` is kept on purpose. |
| 2026-09-16 | `10c4160` | **QR login on a single phone** | A user with only one phone can't scan the QR shown on that same phone. Added `_qr_keyboard()` with the button «📱 فقط همین گوشی را دارم» (`acc:sms`). The texts now explain that the code arrives inside Telegram, not by SMS. |
| 2026-10-05, 10-31 | `ef0680d`, `267533d` | Performance review | Added `tools/review.py` (with `FAIL_EVENTS` and a last-7-days rate). The September incident was skewing the 90-day success rate. |
| 2026-10-31 | `00f22bd`, then `42c14dd` | **MessageMediaUnsupported incident** (task 2, post 165031) | The post's type is newer than Telethon layer 193, so `MEDIA_EMPTY` led to `TypeError: Cannot use [] as file`. The sweeper re-queued it every 180s: 86 retries, 77 "not sent" notices, about 430 wasted sends. The fix in `telkap/services/copier.py` (two adversarial review rounds) added: `Unsendable`, `opaque_media`, `is_permanent` and `_PERMANENT_ERRORS`; `_forward_opaque` / `_forward_without_caption` (raw `ForwardMessagesRequest(drop_author=True, drop_media_captions=…)`); retries limited to the failed target (`process(only_target=)`); a guard so no post is processed twice concurrently; and a persistent `AbandonedPost` table (`telkap/services/abandoned.py`, kept 7 days) that the sweeper checks. Also fixed: the sweeper published posts that were waiting for manual approval (`sweeper._held`); a single-file reupload bug sent posts up to 5 times; and quota leaks. Added `tools/stuck.py`. Tests: `tests/test_unsendable.py` and `tests/test_unsendable_review.py`. |

## 5. Open items and roadmap ideas (never built)

**Code issues still open**
- **`telkap/services/copier.py:Copier._dispatch` drops posts on FloodWait.** It logs the error, records health and sleeps `min(seconds, 300)`, but does not queue the post for retry. The sweeper may recover it later only if the post is still within `LOOKBACK=30` and above the task's watermark (UNSURE how reliable that is).
- **The sweeper re-processes filter-skipped posts.** `copier.process` handles filter or active-hours skips with `_bump(skipped=True)` plus a `skip` activity event, and does not write `MessageMap` or `abandoned.mark` for them. `sweeper._sweep` therefore picks these posts up again every 180s until a later post moves the watermark past them, which inflates `skipped_count`, `DailyStat.skipped` and the `skip` events. Suggested fix: mark them as abandoned with a "filtered" reason, or skip them in the sweeper.

**Owner's own to-dos**
- A dedicated phone number for the service account.
- An online-gateway merchant code. ZarinPal is implemented (enter the code in the admin panel `sys:zarin`, or `ZARINPAL_MERCHANT` in `.env`); Zibal was discussed but is not implemented.
- `AI_API_KEY`, so the AI features work (`docs/AI-MODELS.md`, `tools/ai_models.py`).
- The bot's profile picture in BotFather (`tools/avatar.py`, `e9570ef`).
- An optional server reboot: the server reported "System restart required" and memory and swap were high. Suggested command: `shutdown -r 01:30` (UTC), cancelled with `shutdown -c`.

**Roadmap ideas from the comparison with Telkap's new features** (see also `docs/roadmap.html`)
- Publishing to Bale, Eitaa and Rubika. Bale is the easiest because its API is Telegram-compatible. Eitaa is send-only. Rubika has a Bot API.
- Detection in under 10 seconds by polling hot sources with `getChannelDifference`. Detection, not sending, is the bottleneck: 3-day latency p50 was about 82s and p90 about 163s.
- Scheduled ad posting with pin, auto-delete and stats.
- Sales-page wording for copying protected content and forwarding with tag. Both already work; the page just doesn't say so.
- Further items listed in `docs/roadmap.html`: USDT payment auto-confirmation, PostgreSQL migration, backups outside Telegram, webhooks instead of polling, a publishing calendar.

**Deliberately not built** (per `docs/roadmap.html`): several accounts per user, hosting on Telegram itself.