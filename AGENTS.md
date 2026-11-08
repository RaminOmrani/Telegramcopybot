# AGENTS.md: start here (handoff for AI coding agents)

This file is for any AI agent (Claude, Codex, Cursor, Copilot…) that picks up this project
with nothing but GitHub access. Read this whole file before changing anything. It takes about
five minutes, and the rules here come from real incidents.

Written 2026-11-08 against `main` @ `42c14dd`. **The code is the source of truth.** If this
file and the code disagree, trust the code, then fix this file in the same commit.

| Read next | What's in it |
|---|---|
| [`docs/handoff/ENGINE.md`](docs/handoff/ENGINE.md) | Copy engine: how a source post reaches destinations, background workers, invariants, known limits |
| [`docs/handoff/UI.md`](docs/handoff/UI.md) | Bot UI: `main.py`, routers and their order, callback prefixes, login, simple mode, web panel, Mini App, site |
| [`docs/handoff/BUSINESS.md`](docs/handoff/BUSINESS.md) | Plans, quotas, payments, wallet, referral, reseller, admin roles, rules enforced by tests |
| [`docs/handoff/OPERATIONS.md`](docs/handoff/OPERATIONS.md) | Env vars, DB and migrations, every model, deploy (`update.sh`, systemd), server tools, tests, CI |
| [`docs/handoff/HISTORY.md`](docs/handoff/HISTORY.md) | Timeline of incidents and fixes with commit hashes, the owner's decisions, open items, roadmap |
| [`README.md`](README.md) | User-facing feature list and setup guide (Persian) |

---

## 1. What this is

**«فورواردبات» (ForwardBot)** is a Persian-language Telegram SaaS. It copies posts from source
channels to the customer's destination channels, live. Along the way it can apply text rules,
filters, signature/header/footer, watermark, de-duplication, edit and delete sync, approval
queues and scheduling. Customers buy subscriptions inside the bot (card-to-card, ZarinPal, USDT/TRX).
The owner uses it for their own channels and sells it to others. Production bot: `@AdvancedForwardBot`.

**Stack:** Python 3.11+ (server runs 3.13) · aiogram 3.31 (bot UI, `parse_mode=HTML`) ·
Telethon 1.38.1 (MTProto user accounts, API layer 193) · SQLAlchemy 2 async + SQLite (WAL) ·
aiohttp (web panel, Mini App API, AI) · systemd on a Linux VPS. No Alembic, no Redis, single process.

**Two task modes** (`Task.mode`):
- **full**: the customer logs in their *own* Telegram account (QR or code). That account reads
  the source and sends to the destination. It supports private sources, Premium emoji, forwarding
  and history copy.
- **simple**: no customer login. Our own *service accounts* (`services/pool.py`) read **public**
  sources by polling (`services/pubpoll.py`), and the **bot itself** posts (`services/botsend.py`).
  Premium emoji, private sources, polls and some new post types are not possible here. The owner
  decided users should be told plainly that those need full mode.

## 2. Hard rules (do not break these)

1. **Never ask the owner, or anyone, to paste secrets into chat.** That covers `BOT_TOKEN`,
   `API_ID`, `API_HASH`, `FERNET_KEY`, `AI_API_KEY`, `TRON_API_KEY`, the ZarinPal merchant code,
   server and panel passwords, and SSH private keys. Tell them to put the value directly into
   the server's `/opt/telkap/.env` or into the bot's admin panel. A wallet private key or seed
   phrase must never enter this project.
2. **The repo is PUBLIC.** Never commit secrets, `.env`, `*.session`, `data/`, DB files,
   backups, or personal data about the owner or customers. `.gitignore` covers these; keep it so.
3. **Never copy `.env.example` over `.env`.** That wipes `FERNET_KEY`, which makes every stored
   Telegram login undecryptable, so every customer must log in again. Use `tools/envsync.py`.
   `tests/test_deploy.py` enforces this.
4. **No refunds, ever.** The business only *receives* money. It never pays customers, and no text
   may promise a refund or a payout (`عودت وجه`, `واریز به حساب`, …).
   `tests/test_terms.py` enforces this for the bot texts and `site/index.html`.
5. **Never log into the owner's Telegram account** through third-party tools or connectors.
   That account owns their income channels.
6. **Never run two programs on the same Telegram session at once.** The result is
   `AUTH_KEY_DUPLICATED` and a dead session. Server tools that open a session (`tools/stuck.py`,
   and `tools/tryopen.py`, which has no guard) must run only while `telkap` is stopped.
7. **Don't promise what Telegram can't do.** Example: Premium emoji in simple mode. Bot API
   custom emoji require a Fragment-username bot and don't work in channels anyway.
8. **Git:** never force-push. Never push unreviewed or failing work to `main`. The server deploys
   `main` directly, so `main` must always be green.
9. **HTML everywhere.** The bot uses `parse_mode=HTML`, so `html.escape()` every user- or
   Telegram-supplied string (titles, refs, names, error texts) before interpolating it.
   Several old handlers still don't; fixing them is welcome.

## 3. Working with the owner

- **Talk to the owner in plain, non-technical Persian (فارسی).** They do not read English: an
  English report was answered with «فارسی بگو نمیفهمم چی میگی». Code comments and commit messages
  in this repo are Persian too. Keep that style: comments explain the *why*, often with the
  incident behind it.
- The owner is on **Windows**. They connect with `ssh root@<server>` from PowerShell
  (`deploy/UPDATE.md` has the walkthrough). The hosting panel's VNC console is the fallback and
  **cannot display Persian**, so server tools print ASCII and take `--send`, which delivers the
  report to the admins in Telegram.
- **Deploying an update** (the owner runs it; you tell them when):
  ```
  sudo bash /opt/telkap/deploy/update.sh
  ```
  It compiles first, stops the service, backs up the DB, fast-forwards to `origin/main`,
  installs packages only if requirements changed, restarts, and rolls back automatically on
  failure. Do not use `/opt/telkap/update.sh`: that path does not exist.
- **Diagnostics on the server** (read-only, safe while running):
  ```
  cd /opt/telkap && sudo -u telkap /opt/telkap/.venv/bin/python tools/review.py --days 7 --send   # performance review
  cd /opt/telkap && sudo -u telkap /opt/telkap/.venv/bin/python tools/why.py --send               # why a task isn't copying
  cd /opt/telkap && sudo -u telkap /opt/telkap/.venv/bin/python tools/simplecheck.py --send       # simple-mode health
  ```
  Logs: `journalctl -u telkap -f`. More tools are listed in `docs/handoff/OPERATIONS.md` §7.
- Only ask the owner to deploy work that is finished, tested and on `main`. They expect a short
  Persian message: what changed, in user terms, plus the command.

## 4. Developing

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export BOT_TOKEN=1:x API_ID=1 API_HASH=x ADMIN_IDS=1
export FERNET_KEY=$(.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
.venv/bin/python -m pytest -q -p no:cacheprovider          # ~1200 tests, about 3 minutes
.venv/bin/python -m pyflakes telkap/ tests/ tools/ && .venv/bin/ruff check telkap/ tests/ tools/
```
CI (`.github/workflows/tests.yml`) runs the same three checks on 3.11 and 3.13. These are dummy
values; no real Telegram connection is ever made in tests.

**Test fakes:** `tests/test_copier.py` provides `FakeClient`, `FakeManager`, `Notes` and
`_setup(tmp_path, monkeypatch, settings=…)`, which creates a temp DB, user 7 with a "month" plan,
and a task from -1001 to -1002. `tests/test_unsendable.py:Telethonish` is a more faithful
Telethon fake (MEDIA_EMPTY, `TypeError` on empty file lists, real temp files).

**Expected workflow for a change:**
1. Branch from `main`. Read the Persian comments around the code you touch; they record why
   it is the way it is.
2. Write a test that fails on the current code and passes with your fix. The repo's habit is
   to also prove it fails on the old code; `git worktree` makes that easy.
3. Run the full suite + pyflakes + ruff.
4. For risky changes (copier, retry, sweeper, payments), get an independent review before
   merging.
5. Merge to `main` as a clean commit with a Persian message, then tell the owner (in Persian) to
   run `update.sh`.

**Schema changes:** a new table needs only the model in `telkap/models.py`; `create_all` makes
it at startup. A new column on an existing table also needs an entry in
`telkap/db.py:_add_missing_columns`. New indexes go in `_finish_schema`. Details:
`docs/handoff/OPERATIONS.md` §4.

**Price changes:** after editing `telkap/plans.py`, run `python tools/site.py`. A test checks
that `site/index.html` matches.

## 5. Map of the code (where to look)

```
telkap/main.py              startup: DB, bot, routers, middlewares, ~17 background workers
telkap/config.py            all env vars (names + defaults); .env.example documents them in Persian
telkap/models.py            every table (see docs/handoff/OPERATIONS.md §5)
telkap/db.py                init_db, lightweight migrations, log_activity
telkap/handlers/            aiogram routers; ORDER in handlers/__init__.py:build_router matters
telkap/keyboards.py         menus; button colours: GO (max one per screen), DANGER (irreversible), CALM
telkap/texts.py, i18n.py    Persian texts, fa_num(); 9 languages for the main user path
telkap/services/copier.py   THE copy engine (process → gates → _deliver → _send paths)
telkap/services/userbot.py  full-mode Telethon clients, login, live event handlers, catch_up, heal
telkap/services/sweeper.py  polls sources every 180s because live updates are unreliable (~92% of deliveries)
telkap/services/retry.py    retry queue worker (backoff 60/300/900/3600s, 4 attempts, then abandoned)
telkap/services/pending.py  approval + schedule queue (hold / release / reject / prune)
telkap/services/abandoned.py posts that must never be sent again (sweeper checks this)
telkap/services/pubpoll.py, pool.py, botsend.py, reader.py   simple mode
telkap/services/entitlement.py, subscription.py, payments.py, plans.py   money and quotas
telkap/web/                 aiohttp admin panel (/panel) and Mini App API (/app/api)
site/                       static sales page + Mini App frontend (served by nginx)
deploy/                     update.sh, systemd unit, Linux/Windows setup guides (Persian)
tools/                      server diagnostics (ASCII output, --send to Telegram)
tests/                      ~60 files, pytest-asyncio strict mode
```

## 6. Things that will bite you

- **Telegram doesn't send live updates for big public channels the account doesn't own**
  (Telethon #4345). That's why `sweeper.py` exists. Delivery latency is about half the sweep
  interval (p50 ≈ 80s). Detection, not sending, is the bottleneck.
- **`StringSession` stores no entity cache.** After a restart, any call with a raw numeric chat
  ID fails with "Could not find the input entity". Use `services/reader.py:entity_for`.
- **Telethon is pinned at layer 193.** Newer post types arrive as `MessageMediaUnsupported` and
  can only be copied by server-side forwarding (`copier._forward_opaque`, full mode only).
  Upgrading Telethon is a real project; test it carefully.
- **Queue workers treat `process() == False` as "filtered, drop the item".** If you add a new
  reason for `process` to return False, check `retry.py`, `pending.py`, `sweeper.py` and
  `pubpoll.py`.
- **The sweeper re-sends anything that isn't in `MessageMap`, the retry queue, the pending queue
  or `abandoned_posts`.** Any new "don't send this" outcome must be recorded in one of those, or
  the post comes back every 3 minutes. This caused the 2026-10-31 incident: one post retried
  86 times.
- `Copier._dispatch` sleeps on FloodWait and does not queue the post. Filter-skipped posts above
  the sweep watermark are re-evaluated every sweep, which inflates the skip counters. Both
  issues are known and still open (see HISTORY.md §5).
- In-memory state (queues, `_in_flight`, rate limiter, FSM states) is lost on restart, which
  happens on every deploy.

## 7. Open work (as of 2026-11-08)

Details and context: `docs/handoff/HISTORY.md` §5 and `docs/roadmap.html`.
- **Code issues:** FloodWait in `_dispatch` loses the post; filter-skipped posts are
  re-processed by the sweeper; escape titles in old handlers (`tasks.py:show_task`,
  `simple_task.py`); a possible approval + `min_gap_seconds` double-approval
  (ENGINE.md §9); `pool.revive` has no callers (flooded service accounts never come back).
- **Owner to-dos (they do these themselves):** a dedicated number for the service account; the
  ZarinPal merchant code; `AI_API_KEY` to enable AI features; the bot avatar in BotFather.
- **Roadmap ideas, not built:** publishing to Bale, Eitaa and Rubika (Bale is Telegram-API
  compatible); detection under 10s by polling hot sources with `getChannelDifference`;
  scheduled ad posts with pin, auto-delete and stats; telling buyers on the sales page that
  protected-channel copy and forward-with-tag already work.

Keep this file current. When you finish a piece of work, update §7 and HISTORY.md in the same
commit.
