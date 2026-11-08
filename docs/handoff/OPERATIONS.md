> Part of the AI handoff docs — start at [`AGENTS.md`](../../AGENTS.md).
> Written 2026-11-08 against `main` @ `42c14dd`. Code is the source of truth: if this file and the
> code disagree, trust the code and fix this file. "UNSURE" = not verified by running it.

# Infrastructure & Workflow Handoff: telkap (Telegramcopybot)


## 1. Git branches
- **`main`** is what the server deploys (`deploy/update.sh` defaults to `BRANCH=main`, `update.bat` resets to `origin/main`, `deploy/SETUP-LINUX.md` clones `-b main`).
- **`claude/telkap-bot-clone-5ct7ru`** is the AI dev branch. It contains `main`'s history up to `00f22bd`, the two original 2026-08-02 commits (`a90b917`, `9f18859`) merged in at `2fbe5cb`, and WIP commits. Do not merge it into `main`; treat `main` as the truth and branch new work from `main`.
- CI is `.github/workflows/tests.yml`. It runs on push to `main`, on PRs and on manual dispatch, with Python 3.11 and 3.13 (the comment says the server runs 3.13). Steps: `pyflakes telkap/ tests/ tools/`, then `ruff check telkap/ tests/ tools/`, then `pytest -q` with dummy env vars and a FERNET_KEY generated inside the job.

## 2. Configuration: `telkap/config.py`
- `load_dotenv()` runs at import time. `Settings` is a frozen dataclass.
- `load_settings()` raises `RuntimeError` if any of **BOT_TOKEN, API_ID, API_HASH, FERNET_KEY** is missing.
- The module-level `settings` is only built if `BOT_TOKEN` is set. Use `get_settings()` for lazy access.
- Tests reassign `telkap.config.settings = config.load_settings()` after changing env vars.
- No other module reads `os.getenv` (checked across `telkap/`).

| Env var | Meaning | Default |
|---|---|---|
| BOT_TOKEN | aiogram bot token (BotFather) | required |
| API_ID / API_HASH | Telegram app credentials for Telethon user accounts | required |
| FERNET_KEY | Fernet key that encrypts session strings | required |
| ADMIN_IDS | comma-separated numeric admin IDs; these always have full access | "" |
| DATABASE_URL | SQLAlchemy URL | `sqlite+aiosqlite:///data/telkap.db` (relative to the repo root; this is intentional, see test_deploy) |
| BOT_USERNAME / SUPPORT_USERNAME | used for links and the support handle (`@` stripped) | "" |
| FORCE_JOIN_CHANNEL | initial forced-join channel; moved into the `force_join_channels` table on first run | "" |
| TRIAL_DAYS | on/off switch for the automatic signup trial (0 = off, >0 = on). The trial *length* is the `trial` plan's `days` (default 1, editable in the admin panel), not this value | 1 |
| RATE_PER_MINUTE | max messages per minute per destination | 20 |
| DOWNLOAD_DIR | temporary media directory (relative paths resolve from BASE_DIR) | `data/downloads` |
| LOG_LEVEL | logging level | INFO |
| TIMEZONE_OFFSET | hours from UTC for scheduling | 3.5 |
| CARD_NUMBER / CARD_HOLDER | card-to-card payment details; empty means users are sent to support | "" |
| BACKUP_HOURS | interval for automatic DB backups | 12 |
| LOG_RETENTION_DAYS | how long `activity_log` is kept (0 = forever) | 14 |
| PROXY_URL | socks5/http proxy used by both the bot and Telethon | "" |
| DEVICE_MODEL / SYSTEM_VERSION / APP_VERSION | Telethon client fingerprint | Desktop / Windows 10 / 4.16.8 |
| BACKUP_CHAT_ID | off-server backup channel; the admin-panel `AppSetting` takes precedence | "" |
| WEB_ENABLED | turns on the in-process aiohttp web panel (`_flag`: 1/true/yes/on) | false |
| WEB_HOST / WEB_PORT | web panel bind address and port | 127.0.0.1 / 8080 |
| WEB_BASE_URL | public URL; **must end in `/panel`**; login and Zarinpal callback URLs are built from it | "" |
| AI_BASE_URL | any OpenAI-compatible endpoint | `https://api.avalai.ir/v1` |
| AI_API_KEY | when empty, every AI feature is hidden | "" |
| AI_MODEL_SMALL / MAIN / VISION / EMBED | model names per role (see `docs/AI-MODELS.md`) | gpt-5-nano / qwen3.5-flash / qwen3.5-flash / text-embedding-3-small |
| ZARINPAL_MERCHANT | Zarinpal gateway merchant code; empty hides the gateway | "" |
| TRON_API_KEY | TronGrid key for automatic USDT verification; optional | "" |

`.env.example` documents the same keys in Persian. `tools/envsync.py` treats it as the source of truth for new keys.

## 3. Encryption: `telkap/crypto.py`
- `encrypt(plain)` and `decrypt(token)` use Fernet with `FERNET_KEY`. `decrypt` returns `None` on `InvalidToken`.
- It encrypts `User.session_enc` (customer Telethon sessions) and `ServiceAccount.session_enc`.
- Used in `telkap/services/userbot.py`, `services/svclogin.py` and `services/pool.py`.
- **If FERNET_KEY is lost or changed, every stored login is unreadable and every user must log in again.**
- Never copy `.env.example` over `.env`, never regenerate the key, and back up `.env` alongside `data/`.
- Do not confuse this with `telkap/services/crypto.py`, which handles USDT/Tron payments.

## 4. Database: `telkap/db.py`
`init_db()` does the following, in order:
1. `_ensure_sqlite_dir` creates the database directory.
2. `create_async_engine` builds the engine.
3. `_tune_sqlite` sets per-connection PRAGMAs: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`.
4. Inside one transaction it runs `_prepare_schema` (a legacy `message_map` rename), then `Base.metadata.create_all`, then `_finish_schema(conn, migrated)`.

`_finish_schema` calls `_add_missing_columns`, creates several indexes with `CREATE INDEX IF NOT EXISTS`, and copies data from `message_map_legacy` if that migration ran.

Other helpers: `get_session()` (async context manager), `session_factory()`, `close_db()`, and `log_activity(...)`, which writes `ActivityLog` and never raises.

There is no Alembic. To change the schema safely:
- **New table:** add the model to `telkap/models.py`. `create_all` creates it on the next start.
- **New column on an existing table:** add it to the model and also add `(name, "SQL TYPE DEFAULT x")` to the `additions` dict in `db.py:_add_missing_columns`. `create_all` does not alter existing tables. Pick the default carefully, because existing rows get it. For example, `tasks.mode` defaults to `'full'` and `delivery_timings.via` defaults to `''` on purpose.
- **New index on an existing table:** add a `CREATE INDEX IF NOT EXISTS` line in `_finish_schema`.
- **Rename or drop a column:** SQLite needs a table rebuild. Follow the `_prepare_schema` rename-to-legacy-and-copy pattern.

## 5. Models (`telkap/models.py`, class → table)
- `User` → `users`: Telegram user (id = Telegram ID). Holds encrypted session, credits, wallet, reseller fields, account state, language, terms version.
- `Subscription` → `subscriptions`: a user's plan subscription.
- `Task` → `tasks`: one copy job, source to main destination. Has `source_kind` (telegram/rss), `mode` (full/simple) and a `settings` JSON.
- `Rule` → `rules`: text rules per task (replace, regex, filter).
- `ForwardProfile` → `forward_profiles`: "advanced forward" profile. The user forwards a message to the bot and the bot applies settings and posts it.
- `Destination` → `destinations`: extra destinations for a task, with per-destination `overrides`.
- `MessageMap` → `message_map`: source-to-destination message mapping, used for edit/delete sync and dedupe (`content_hash`, `norm_hash`, `simhash`).
- `RetryItem` → `retry_queue`: failed sends waiting for retry.
- `PendingPost` → `pending_posts`: posts waiting for approval or a scheduled time.
- `AbandonedPost` → `abandoned_posts`: posts that must never be sent (for example unsendable). The sweeper respects these.
- `ForceJoinChannel` → `force_join_channels`: channels users must join.
- `PaymentRequest` → `payment_requests`: plan, credit or wallet purchases (card, USDT, Zarinpal).
- `DailyStat` → `daily_stats`: per-task daily post counts.
- `PeriodUsage` → `period_usage`: quota usage per subscription period.
- `ReminderState` → `reminder_state`: stops expiry reminders from repeating.
- `SupportTicket` / `SupportMessage` → `support_tickets` / `support_messages`: support conversations.
- `WalletEntry` → `wallet_entries`: wallet ledger.
- `ReferralReward` → `referral_rewards`: referral reward lifecycle.
- `Coupon` / `CouponUse` → `coupons` / `coupon_uses`: discount codes and their redemptions.
- `GiftCode` → `gift_codes`: single-use gift or presale codes.
- `ResellerSale` → `reseller_sales`: reseller purchases made for customers.
- `PlanOverride` → `plan_overrides`: admin edits to plans defined in `telkap/plans.py`.
- `AppSetting` → `app_settings`: key/value settings editable from the admin panel (for example the backup chat ID).
- `ActivityLog` → `activity_log`: audit and event log; `actor_id` is who did it, `user_id` is who it affected.
- `AdminRole` → `admin_roles`: roles for extra admins (owner and others).
- `WebAccount` / `WebSession` → `web_accounts` / `web_sessions`: web panel logins and sessions.
- `ChurnFeedback` → `churn_feedback`: why users did not renew.
- `DeliveryTiming` → `delivery_timings`: source-to-destination latency, with a `via` path.
- `ServiceAccount` → `service_accounts`: our own Telethon accounts that read public sources for "simple mode".
- `SourceLease` → `source_leases`: which public source is read by which service account.

## 6. Deployment
**`deploy/telkap.service`** (systemd unit):
- `User=telkap`, `WorkingDirectory=/opt/telkap`, `EnvironmentFile=/opt/telkap/.env`.
- `ExecStart=/opt/telkap/.venv/bin/python -m telkap.main`, `Restart=always`, `RestartSec=10`.
- Hardening: `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `NoNewPrivileges`. The only writable path is `ReadWritePaths=/opt/telkap/data`.
- Logs: `journalctl -u telkap -f`.

**`deploy/update.sh`** (run as `sudo bash /opt/telkap/deploy/update.sh`). Overridable variables: APP_DIR=/opt/telkap, APP_USER=telkap, SERVICE=telkap, BRANCH=main, KEEP_BACKUPS=10. Steps:
1. Re-executes itself from a temp copy (guarded by `TELKAP_SELF_COPY`) so that `git pull` cannot rewrite the script while bash is reading it. Must run as root.
2. Records `OLD_COMMIT`. Aborts if `git diff` shows uncommitted changes on the server. Runs `git fetch origin $BRANCH`.
3. Exits early only if HEAD equals `origin/$BRANCH` **and** `data/.deployed` holds that same commit. `data/.deployed` records the commit that actually started successfully, so a manual `git pull` still forces a restart.
4. Before stopping the bot: extracts the new commit with `git archive` into a temp dir and runs `compileall`. On a syntax error it aborts and leaves the bot running. It also checks whether `requirements.txt` changed.
5. Runs `systemctl stop`. Then `_backup_db` copies `data/telkap.db` plus `-wal`/`-shm` to `data/backups/telkap-<stamp>.db` and keeps the newest `KEEP_BACKUPS` (10) `.db` files (old `-wal`/`-shm` copies are not pruned). Then `git merge --ff-only`. Then `pip install -r requirements.txt`, only if requirements changed.
6. Runs `tools/envsync.py` to report missing keys. Never writes `.env`.
7. Runs `reset-failed`, then `start`, then waits up to 12 s for the service to be active with `NRestarts=0`. On success it writes `.deployed` and prints the downtime.
8. On failure it prints 25 journal lines, runs `git reset --hard OLD_COMMIT`, reinstalls packages and restarts.

**Other deploy files:**
- `deploy/web-setup.sh DOMAIN EMAIL [ALIASES…]` installs nginx and certbot. It backs up `.env`, then sets `WEB_ENABLED=true`, `WEB_HOST=127.0.0.1`, `WEB_PORT` and `WEB_BASE_URL=https://DOMAIN/panel`, and restarts the service. The nginx site at `/etc/nginx/sites-available/telkap` serves: `/` as the static sales page from `site/`, `/panel` proxied to the bot, `/app` as the static mini-app from `site/app/`, and `/app/api` proxied to the bot.
- `deploy/ssh-port.sh [ports]` adds extra SSH ports (default 8443 2083 2087 2222) and keeps 22 open. It handles Ubuntu 24 socket activation and uses ufw.
- `deploy/ssh-harden.sh KEEP_PORT` sets `PasswordAuthentication no` and `PermitRootLogin prohibit-password` in `sshd_config.d/98-harden.conf`, and prunes extra ports. **Run it only after key-based login works.**
- `deploy/SETUP-LINUX.md` is the full Ubuntu VPS guide (Persian): create the `adduser --system telkap` user, clone to `/opt/telkap`, create the venv, bring over the old `.env` and `data/` (it warns never to copy `.env.example`), install systemd, run web-setup and ufw, set up backups, and update with `update.sh`.
- `deploy/UPDATE.md` is the Windows-to-server update walkthrough. `SETUP-WINDOWS.md` and `WINDOWS.md` cover the older Windows/NSSM hosting.
- **Docker** (`Dockerfile`, `docker-compose.yml`): python:3.11-slim plus fonts-dejavu; copies only `telkap/`; runs `python -m telkap.main`; mounts `./data:/app/data` and reads `env_file: .env`. Production does not appear to use it; production uses systemd (UNSURE whether Docker is maintained). Because only `telkap/` is copied, `site/` and `tools/` are not inside the image.

**Windows `.bat` helpers** (all run `cd /d %~dp0` and use `.venv\Scripts\python.exe`):
- `setup.bat`: creates the venv (`py -3 -m venv`), installs requirements, and copies `.env.example` to `.env` **only if `.env` does not exist yet**. The tool wrappers fall back to `py -3` when the venv is missing.
- `run.bat`: runs `python -m telkap.main`; requires `.env`.
- `update.bat`: `git fetch origin main`, then `git reset --hard origin/main`, then pip install, then tells you to run `nssm restart TelkapBot`.
- `backup.bat`: zips `data` and `.env` into `telkap-backup-<stamp>.zip`. The zip contains secrets and is in `.gitignore`.
- `envsync.bat [--apply]`, `nettest.bat`, `ai-models.bat [filter]`: wrappers around the matching tools.

## 7. Tools (`tools/*.py`)
Run them on the server as `sudo -u telkap /opt/telkap/.venv/bin/python /opt/telkap/tools/X.py`. The DB tools call `os.chdir` to the repo root (enforced by a test). Tools that print pure ASCII do so because the VNC console cannot show Persian. `--send` sends the report to ADMIN_IDS as a document through the Bot API (`sendDocument`).

- `envsync.py [--apply]`: lists `.env.example` keys missing from `.env` and appends them only with `--apply`, after making a backup. Prints key names only, never values.
- `why.py [--send] [--speed] [--clear-retries]`: reads only the DB and explains why tasks are not copying. `--clear-retries` deletes the retry queue.
- `review.py [--days N] [--send]`: reads only the DB and produces a performance review (default 30 days).
- `simplecheck.py [--send]`: simple-mode status (service accounts, leases, destinations). Reads the DB and calls Bot API `getMe`/`getChatMember`. Does not start Telethon.
- `stuck.py <task_id> [--send]`: reads the last 40 source posts through Telethon using the **same user session as the bot**. **It refuses to run while the `telkap` service is active** (`bot_is_running()`, which assumes "running" if unsure). An override flag `--i-know-the-bot-is-running` exists; never use it. Usage: `systemctl stop telkap`, then run the tool, then `systemctl start telkap`.
- `tryopen.py [@ch …] [--send]`: tests whether public channels can be read without joining, through `manager.ensure_client` using the first user who has `session_enc`. **It has no service-active guard.** It uses a live user session concurrently with the bot, which risks Telegram invalidating that session. Stop the service first (UNSURE that it is harmful, but treat it like `stuck.py`).
- `botcheck.py`: shows which bot the `.env` token belongs to, printing a fingerprint and never the token.
- `nettest.py`: checks Telegram reachability and the proxy. Standard library only.
- `ratetest.py`: diagnoses fetching of the USDT exchange rate in three layers (DNS, DoH, HTTP).
- `ai_models.py [filter] [--json]`: lists the AI provider's models and writes `ai-models.json` (git-ignored).
- `site.py [--check]`: regenerates the pricing section of `site/index.html` from `telkap/plans.py`. `tests/test_terms.py` runs `--check`, so **after changing prices you must run `python tools/site.py`**.
- `preview.py [outdir]`: renders sample web-panel pages as standalone HTML (default output `data/preview`).
- `avatar.py`: generates bot avatar images into `site/brand/`.

## 8. Tests (`tests/`, 60 files, pytest-asyncio in strict mode)
- **Config** lives in `pyproject.toml`: `testpaths=["tests"]`, `asyncio_mode="strict"` (each async test needs `@pytest.mark.asyncio`), and DeprecationWarnings are ignored.
- **`tests/conftest.py`**: an autouse fixture clears `telkap.services.cache` before and after each test.
- **Feature-area test files**: `test_v3.py` to `test_v16.py`, plus files such as `test_pool.py`, `test_sweeper.py`, `test_retry_loop.py`, `test_zarinpal.py` and `test_webauth.py`.
- **`tests/test_copier.py` fakes:**
  - `FakeClient` mimics Telethon: `get_input_entity`, `send_message`, `send_file`, `forward_messages`, `edit_message` and `delete_messages` record `SentRecord`s.
  - `FakeManager(client)` mimics the client manager: `ensure_client` returns the fake, `tasks_for_chat` returns `[]`, and `reload_user` records calls.
  - `Notes` collects notifications sent to users.
  - `_setup(tmp_path, monkeypatch, settings=…, rules=…)` points `DATABASE_URL` at `tmp_path/copier.db`, rebuilds `config.settings`, runs `init_db()`, creates `User(id=7)`, calls `subscription.grant(7, "month")`, and creates a `Task` (source -1001, destination -1002) plus `Rule`s. It returns `(db_module, task_id)`.
  - `_copier(...)`, `_Event`, `_Msg` and `_NoTasks` test album and ordering logic with a patched dispatch.
- **`tests/test_deploy.py` guards** (static checks on the text of `update.sh`):
  - new code is compiled before the stop;
  - dependencies are installed only when they changed;
  - the backup happens after the stop;
  - downtime is printed;
  - the script never contains `cp .env.example .env` or `cp -f .env.example`;
  - rollback (`git reset --hard`) comes after `systemctl start`;
  - the tools why, botcheck, tryopen, simplecheck, review and stuck each contain `os.chdir(`;
  - the default `database_url` stays relative.

**Running the suite locally:**
```
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
export BOT_TOKEN=1:x API_ID=1 API_HASH=x ADMIN_IDS=1
export FERNET_KEY=$(.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
.venv/bin/python -m pytest -q -p no:cacheprovider
.venv/bin/python -m pyflakes telkap/ tests/ tools/ && .venv/bin/python -m ruff check telkap/ tests/ tools/
```
Ruff settings: line-length 110, target py311, `select = E, F, W, I, UP, B`, `ignore = B008`.

## 9. Requirements (pinned)
- `requirements.txt`: aiogram==3.31.0, Telethon==1.38.1, SQLAlchemy[asyncio]==2.0.36, aiosqlite==0.20.0, cryptography==44.0.0, Pillow==11.0.0, segno==1.6.1 (QR login), python-dotenv==1.0.1, aiohttp==3.10.11 (web panel and AI), aiohttp-socks==0.10.1, python-socks[asyncio]==2.7.1, APScheduler==3.10.4.
- `requirements-dev.txt`: everything in `requirements.txt` plus pytest==8.3.4, pytest-asyncio==0.25.0, pyflakes==3.2.0, ruff==0.8.4.
- Entry point: `python -m telkap.main`, also available as `telkap = telkap.main:run`. Background tasks such as `backup.run_forever(bot)` are started in `telkap/main.py`.

## 10. Runtime data and secrets
- `data/` (git-ignored) holds `telkap.db` with its `-wal`/`-shm` files, `downloads/`, `backups/` and `.deployed`.
- Also git-ignored: `.env`, `.env.bak-*`, `telkap-backup-*.zip`, `*.session`, `*.db`.
- The repo is public. Never commit `.env` and never paste its values anywhere.