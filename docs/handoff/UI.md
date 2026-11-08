> Part of the AI handoff docs — start at [`AGENTS.md`](../../AGENTS.md).
> Written 2026-11-08 against `main` @ `42c14dd`. Code is the source of truth: if this file and the
> code disagree, trust the code and fix this file. "UNSURE" = not verified by running it.

# UI Map: bot handlers, menus, login flows, web/site (handoff)

## 1. Entry point: `telkap/main.py`
- `main()`: `init_db()`, then an optional proxy (`PROXY_URL` → `proxy.for_aiogram`). The `Bot` is built with `DefaultBotProperties(parse_mode=ParseMode.HTML)`, so **every message is HTML**.
- Binding: `alerts.bind(bot)`; `Copier(manager, notifier)`; `manager.bind_copier/bind_notifier`; `HistoryCopier` → `history_handlers.bind`; `ReleaseWorker` → `approval_handlers.bind`.
- `Dispatcher(storage=MemoryStorage())`. FSM state is in memory and is lost on restart.
- Middlewares are registered on both `dispatcher.message` and `dispatcher.callback_query`, in this order: `ErrorLogMiddleware`, `LanguageMiddleware`, `BanMiddleware`, `MaintenanceMiddleware`, `ForceJoinMiddleware`. Error logging is outermost.
- Before polling: `planstore.load()`, `forcejoin.seed_from_env()`, `manager.restore_all()` and `manager.listening_summary()`. If restore leaves a gap, an alert goes out with `alerts.send(cap=roles.CAP_SYSTEM, key="restore-gap")`.
- Background tasks: `subscription_watchdog` (every 900s it disables tasks of users with no plan), `retry_worker`, `release_worker`, `reminders`, `digest`, `backup`, `renewal`, `agentnudge`, `maintenance`, `alerts`, `feedworker`, `cryptocheck` ("usdt"), `usdtrate`, `manager.heal_forever`, `sweeper`, `poller` (`PublicPoller`, the simple-mode engine).
- Then `web.start_panel(bot)` (a failure is logged and not fatal), `delete_webhook(drop_pending_updates=True)` and `start_polling`.

## 2. Middlewares: `telkap/middlewares.py`
- `LanguageMiddleware`: `i18n.language_of(uid, fallback=tg language_code)`, then `i18n.set_current` (a ContextVar) and `data["lang"]`.
- `MaintenanceMiddleware`: when `maintenance.mode()` is on, only `roles.is_staff` users get through.
- `BanMiddleware`: blocks users with `User.is_banned`. It does a DB read on every event.
- `CapMiddleware(cap)`: a per-router lock, attached in `telkap/handlers/__init__.py:LOCKED`.
- `ForceJoinMiddleware`: checks `forcejoin.active_channels()` and keeps an in-memory `_verified` set, which resets when the channel list changes. `.env` admins bypass it. It shows join buttons plus `join:check`. If `get_chat_member` fails, it treats the user as a member (fail-open).
- `ErrorLogMiddleware`: logs the exception and replies with a generic "⚠️ خطایی رخ داد" (an error occurred).

## 3. Router order: `telkap/handlers/__init__.py:build_router`
The order matters: stateful flows come before generic text handlers, and the first match wins.

`start, guide, language, admin, admin_users, admin_channels, admin_plans, admin_limits, admin_referral, admin_coupons, admin_reports, admin_system, admin_pool, support, account, tasks, chatpicker, destinations, preview, clone, watermark_wizard, settings, history, wallet, reseller, billing, churn, approvals, simple_task, task_templates, forward`

- `forward` is last on purpose: its `F.forward_origin | F.forward_from | F.forward_from_chat` handler has no state filter.
- `LOCKED` cap guards:
  - `admin_users` → `CAP_USERS`
  - `admin_channels`, `admin_system`, `admin_pool` → `CAP_SYSTEM`
  - `admin_plans`, `admin_limits`, `admin_referral`, `admin_coupons` → `CAP_MONEY`
  - `admin_reports` → `CAP_REPORTS`
- `admin.py` and `support.py` are **not** router-locked. They check rights per handler (UNSURE how consistently; `roles.can_cached` exists for this).
- All FSM states live in one class, `telkap/handlers/common.py:Flow(StatesGroup)`. Groups: login (`phone`, `code`, `password`), task creation, rules, settings `ask_value`, forward, payments, many `admin_*`, `pool_label`/`pool_password`, `simple_source`/`simple_dest`/`simple_title`.
- Helpers in `common.py`: `get_or_create_user`, `require_login`, `require_subscription`, `hash_pin`, `check_pin`, `parse_int`.

## 4. Handler modules (callback_data prefixes)

| Module | Purpose | Prefixes / commands |
|---|---|---|
| `start.py` | `/start` (referral payload `ref_<id>`, first-visit language picker), `/cancel` (also `manager.cancel_login`), activity logs | `acc:logs`, `/logs` |
| `guide.py` | In-bot guide and FAQ. Persian text is built here from plan values; other languages come from `guide_texts.py` | `guide:`, `guide:home`, `/help` `/guide` |
| `language.py` | Language picker (`picker`, `ask_text`) | `acc:lang`, `lang:<code>`, `/lang` |
| `account.py` | Account screen, login (§6), logout, PIN, quota/usage, terms | `acc:login/qr/sms/pass/logout/pin/sub/quota/level/digest`, `terms:ok/show`, `/account` `/login` `/terms` |
| `tasks.py` | Task list, full-mode creation wizard (`Flow.task_source/dest/title`, RSS source too), task screen `show_task`, toggle, delete, stats, "why no post", relisten, pro menu, health | `task:new/list/open:/toggle:/del:/delyes:/stats:/why:/relisten:/pro:/health`, `/tasks` `/newtask` |
| `simple_task.py` | Simple mode (§7) | `simple:new`, `simple:why`, `task:mode:[up:|down:]` |
| `settings.py` | Per-task settings: clean, filters, media, text, AI, watermark, send, time, rules | `set:`, `flag:`, `ask:`, `media:`, `mode:`, `order:`, `hour:`, `dupmode:`, `rule:`/`ruleadd:`/`ruledel:`, `wm*:`, `ai*:`, `adsens:`, `noop` |
| `destinations.py` | Multiple destinations, per-destination signature and routing | `dest:add/del/list/sig/toggle:`, `dmain:`, `droute:`, `dsig:` |
| `chatpicker.py` | Pick a chat from the user's own dialogs | `pick:`, `picked:` |
| `preview.py` | Dry-run of the last source post | `task:test:` |
| `clone.py` | Copy settings between tasks; export/import as a file | `clone:pick/from/do/export/import:` |
| `watermark_wizard.py` | Six-preview watermark chooser | `wmpk:`, `wmpv:`, `wmpvnow:` |
| `task_templates.py` | Ready-made setting templates | `tpl:list/show/use:` |
| `history.py` | Copy past posts (archive) | `hist:start/cancel:`, `/progress` |
| `approvals.py` | Queue of posts waiting for approval | `pend:list/ok/no/allok/allno:` |
| `forward.py` | "Advanced forward": the user forwards a post to the bot and the bot republishes it | `fwd:new/open/toggle/del`, `fwdset:`, `fwdflag:`, `fwdask:`, `/forward` |
| `billing.py` | Plans, card-to-card receipts, coupons, credit packs | `plan:`, `plan:long`, `pay:go/ok/no/cpn/cancel`, `paym:`, `credit:*`, `cmp:plans`, `/plans` |
| `wallet.py` | Wallet, top-up, invite page, gift codes, bills | `wal:*`, `/wallet` |
| `reseller.py` | Reseller (agent) panel | `rs:home/pick:/customers/sales/web`, `/reseller` |
| `churn.py` | Answers to "why didn't you renew?" | `churn:` |
| `support.py` | Tickets; the user never sees the admin's ID | `sup:new/view/done`, `supa:view/reply/open/close:`, `adm:tickets`, `/support` |
| `admin.py` | Admin home `admin_menu`, stats, payments, retry queue, backup, grant, ban, broadcast, `/chatid` | `adm:home/stats/pay/payshow:/retry/backup/setbk:/grant/ban/cast`, `cast:`, `castgo`, `/admin` `/stats` `/grant` `/ban` `/unban` `/broadcast` |
| `admin_users.py` | User browser and per-user actions | `adm:users`, `admu:*`, `admul:` |
| `admin_channels.py` | Force-join channels | `adm:join`, `fj:*` |
| `admin_plans.py` | Edit plans, limits, prices at runtime (`planstore`) | `adm:plans`, `pe:*` |
| `admin_limits.py` | Per-user limit and feature overrides | `ul:*` |
| `admin_referral.py` | Referral settings | `adm:ref`, `rf:set:` |
| `admin_coupons.py` | Coupons and gift codes | `adm:coupons`, `cp:*`, `gf:*` |
| `admin_reports.py` | Revenue, funnel, churn, audit | `adm:reports`, `rep:*`, `/reports` |
| `admin_system.py` | Roles, maintenance, payment settings (card, USDT, Zarinpal), AI, web panel accounts and login link | `adm:sys`, `sys:*` (`sys:roles/radd/rdel:`, `sys:maint`, `sys:web*`, `sys:zarin` …) |
| `admin_pool.py` | Service-account pool for simple mode | `pool:home/add/pass/toggle:/drop:`. Opened by `F.text == "/pool"` (not `Command`) or from `adm:sys` → `pool:home` |

Main menu reply-keyboard texts are matched with `F.text.in_(menu_texts("menu.x"))`, which covers every language's translation of the button.

## 5. Keyboards, texts, i18n
- `telkap/keyboards.py`:
  - `main_menu(lang)`: an 8-button reply keyboard: `menu.new_task` (GO), `tasks`, `forward`, `account`, `plans` (CALM), `wallet`, `help`, `support`.
  - `menu_texts(key)`.
  - Menu builders: `tasks_list`, `task_menu`, `clean_menu`, `filters_menu`, `engagement_menu`, `media_menu`, `text_menu`, `configs_menu`, `ai_menu`, `watermark_menu`, `send_menu`, `time_menu`, `destinations_menu`, `rules_menu`, `confirm(yes, no, danger=)`, `plans_menu`, `long_term_menu`, `credits_menu`, `credit_packs_menu`, `credit_offer_menu`, `account_menu`, `quota_menu`, `forward_menu`, `fwd_clean_menu`, `fwd_text_menu`.
  - `_mini_app_button()` adds a `WebAppInfo` button only when the `https` URL from `web.miniapp.public_url()` exists.
  - The legacy `BTN_*` Persian constants are still used, e.g. `BTN_LOGS`.
- **Button colours** use the Bot API 9.4 `style=` field on `InlineKeyboardButton`/`KeyboardButton`, with constants `keyboards.DANGER="danger"`, `GO="success"`, `CALM="primary"`.
  - Rules (comment at the top of `keyboards.py`): **at most one green (GO) per screen**, meaning "the next step". Red only for irreversible actions (delete, disconnect). If every button on a screen is a delete, none is coloured. CALM marks important but non-urgent doors.
  - A disabled task shows red in `tasks_list`.
  - `confirm(danger=True)` makes "yes" red.
  - Old clients ignore `style`.
  - Tests: `tests/test_botsend.py` (about lines 301–317) checks `confirm` styles. `test_at_most_one_green_button_per_screen` enforces at most one GO on account_menu (both states), quota_menu, plans_menu, long_term_menu and credit_offer_menu; other screens follow it by convention only.
- `telkap/texts.py`:
  - `fa_num(value)`: Persian digits; ints get the `،` thousands separator. Use it for every number shown in Persian.
  - `on_off()`.
  - Persian constants: `START`, `LOGIN_CHOICE`, `LOGIN_QR`, `LOGIN_INTRO`, `LOGIN_CODE`, `LOGIN_PASSWORD`, `LOGIN_OK`, `NO_LOGIN`, `NO_SUBSCRIPTION`, `ASK_*` …
- `telkap/i18n.py`:
  - `LANGS = ("fa","en","ar","ru","tr","uz","hi","id","pt")`, `DEFAULT="fa"`, `RTL={"fa","ar"}`.
  - `t(key, lang, **kw)` falls back to Persian; an unknown key returns the key itself.
  - `num()` gives per-language digits and separators. `plural()` handles ru/ar "few" forms.
  - Language storage: `language_of`, `set_language`, `forget` (cache).
  - The `CATALOG` dict covers only the main user path (welcome, menu, account, purchase, task creation). Admin panel text is Persian only.
- `telkap/guide_texts.py`: non-Persian guide translations (`TITLES`, `BODIES`, `title()`, `body(**fields)` with `{{field}}` placeholders, `coverage()`). Persian guide text lives in `handlers/guide.py`.

## 6. Account login
**User login.** UI in `telkap/handlers/account.py`; engine in `telkap/services/userbot.py`, class instance `manager`.

1. `_begin_login`: if `terms.accepted` is false, it shows `terms.text()` with `terms:ok`. Otherwise it offers a choice: `acc:qr` (recommended) or `acc:sms`.
2. QR path:
   - `cb_login_qr` calls `manager.start_qr_login`, renders a PNG with `segno` (`_qr_png`) and starts the background `_watch_qr`.
   - `_watch_qr` loops `manager.qr_result(uid, 25s)` for up to 180s (`QR_STEP_SECONDS`, `QR_TOTAL_SECONDS`), handling `done`, `password`, `wait` and `gone`.
   - On `wait` it calls `manager.refresh_qr` and edits the photo, re-attaching `_qr_keyboard()` each time. That keyboard holds the "📱 فقط همین گوشی را دارم" ("I only have this phone") → `acc:sms` escape button.
   - A 2FA password need is surfaced as an `acc:pass` button, because the background task has no FSMContext; the button then sets `Flow.password`.
3. Code path: `Flow.phone` (`+countrycode…`) → `manager.start_login` → `Flow.code` (`clean_code`) → `manager.submit_code` → optional `Flow.password` → `manager.submit_password`. The message containing the password is deleted.
4. Quirk (verified): after accepting the terms, `cb_accept_terms` goes straight to `Flow.phone`, so the QR option is never offered on that path.

**Service accounts.** `telkap/services/svclogin.py` is QR-only, keyed by admin ID in the `_pending` dict. Functions: `start`, `refresh`, `result` (`done`/`password`/`wait`/`gone`), `submit_password`, `cancel`. `_finish` upserts `ServiceAccount` by `account_id`, encrypting the session string with `crypto.encrypt`, and the client is then disconnected. It is a separate path from customer login on purpose. UI: `admin_pool.py` (`pool:add` → `Flow.pool_label` → QR, `_watch`, `pool:pass` → `Flow.pool_password`). Tests: `tests/test_svclogin.py`, `tests/test_qr_login.py`.

## 7. Simple mode: `telkap/handlers/simple_task.py`
- Simple mode creates a task with no user account. The source is read by our service-account pool (`services/pool.py`, `pool.any_client()`). The bot itself posts to the destination (`services/botsend.can_post`). The source must be a **public** `@username` channel. `Task.mode` is `Task.MODE_SIMPLE="simple"` or `MODE_FULL="full"` (`models.py`, around line 179).
- Flow:
  1. `simple:new` → `_allowed` checks the plan and `max_tasks`.
  2. `Flow.simple_source`: `_pick_source()` is a `KeyboardButtonRequestChat` with `chat_has_username`; free text is also accepted. `client.get_entity` resolves it, and the ID is normalised to `-100…`.
  3. `Flow.simple_dest`: `_pick_dest()` requests the chat with `bot_administrator_rights=BOT_RIGHTS` (post-only), so Telegram promotes the bot itself. The result is verified with `botsend.can_post`.
  4. `Flow.simple_title` → `Task(mode=MODE_SIMPLE)` → `show_task`.
  5. With an empty pool, the user sees `NO_POOL` and an admin alert goes out (`key="pool-empty-signup"`).
- `MODE_COMPARISON` is the single shared text, used in three places: before the mode choice (`simple:why`), on the task (`task:mode:<id>`), and when a not-logged-in user taps upgrade (`cb_upgrade`). It covers what simple mode supports, what only full mode has (premium emoji, private source, archive), and QR vs code advice.
- `offer(message, uid)` shows the choice (`new_task_choice()`: simple = GO, "with my account" `task:new` = CALM) when the user is not logged in and the pool has capacity.
- Mode switching:
  - Upgrade, `cb_upgrade` (`task:mode:up:<id>`): requires login, `manager.ensure_client` and `manager.resolve_chat_id(source_ref)` before it sets FULL, then `manager.reload_user`.
  - Downgrade, `cb_downgrade` (`task:mode:down:<id>`): requires `source_ref.startswith("@")`, bot `can_post` on the destination, and free pool capacity.
  - `cb_mode` (`task:mode:<id>`) must stay registered **after** up/down because its prefix also matches them.
  - `keyboards.task_menu` shows the mode button only for `@` sources.
- Tests: `tests/test_simple_mode.py`, `tests/test_simple_signup.py`, `tests/test_pubpoll.py`, `tools/simplecheck.py`.

## 8. `telkap/web/` (in-process aiohttp)
- `__init__.py` exports `start_panel` and `stop_panel`. The panel is off unless `WEB_ENABLED=true`. Other env vars: `WEB_HOST` (default 127.0.0.1), `WEB_PORT` (default 8080), `WEB_BASE_URL` (must end in `/panel`). It runs in the same process because of SQLite single-writer locking.
- `server.py:build_app` serves the admin panel under `render.PREFIX="/panel"`:
  - Login and session: `/login`, `/enter?t=` (one-time link from `sys:web`), `/theme`, `/healthz`.
  - Payments: `/payments/{id}/approve|reject`, `/receipt/{id}`.
  - Users, tasks, resellers: `/users…`, `/tasks…`, `/resellers…`.
  - Other pages: `/finance`, `/agent…` (reseller view), `/timings`, `/activity`, `/account`, `/settings/{card,crypto,autorate,ratenow,zarinpal}`.
  - Zarinpal callback route and static fonts (Vazirmatn) under `/panel/static`.
- `auth.py`: username + PBKDF2 password + a two-factor code sent via Telegram, a lockout, and hashed session IDs. The cookie is named `telkap_panel`. Models: `WebAccount`, `WebSession`.
- `render.py`: HTML helpers (`page`, `card`, `table`, `form`, `chart`, `pill`, `money`, `url`) and `esc()` for escaping.
- `miniapp.py`: the Telegram Mini App JSON API under `API_PREFIX="/app/api"`. Endpoints: `ping`, `health`, `plans`, `me`, `stats`, `wallet`, `chats`, `tasks` (CRUD, toggle, settings, rules, dests, clone, logo), `quote/{code}`. Authentication is `_who()`, an HMAC check of `X-Telegram-Init-Data` against the bot token. `public_url()` gives the Mini App URL.

## 9. `site/` (static, served by nginx, not by Python)
- `site/index.html`: the Persian marketing/sales landing page ("فورواردبات (ادمین پست)"), with OG metadata for `forwardbot.softmiliac.com`. Its pricing section is generated by `tools/site.py` from `telkap/plans.py`; `--check` reports a stale page. CI runs `--check` indirectly: `tests/test_terms.py` calls `tools/site.py --check`, so a stale pricing section fails pytest.
- `site/app/index.html`: the Mini App frontend, a single file. It uses `telegram-web-app.js` and calls `fetch('/app/api'+path)` with the `X-Telegram-Init-Data` header.
- `site/brand/`: icons and avatars (from `tools/avatar.py`). `site/fonts/` holds fonts.
- nginx layout in `deploy/web-setup.sh` and `deploy/SETUP-LINUX.md` §7: `/` → `site/`; `/app` → `site/app/` (`try_files`); `/app/api` and `/panel` → proxied to `127.0.0.1:$WEB_PORT`.

## 10. Conventions and pitfalls for new UI work
- Telegram output is HTML everywhere, so **`html.escape()` any user or Telegram-supplied string** (titles, refs, names, error text) before interpolating it.
  - Current code is inconsistent. Only `handlers/preview.py` and `handlers/approvals.py` (plus `services/copier.py`, `feeds.py`, `retry.py`) use `escape`.
  - Raw interpolation exists in, for example, `tasks.py:task_detail_text` (used by `show_task`; lines ~55–59: `task.title`, `source_ref`, `dest_ref`), `simple_task.py:got_source/got_dest` (`<b>{title}</b>`, around lines 304 and 338) and `cb_upgrade` (`task.source_ref`).
  - A title containing `<` or `&` can make Telegram reject the message with "can't parse entities". This is a known risk and a good fix target.
- Numbers go through `fa_num()` (Persian screens) or `i18n.num()` (localised screens).
- User-path strings go through `i18n.t(key)`, with new keys added to `i18n.CATALOG`. Admin screens are Persian literals.
- Colours: one GO per screen at most, DANGER only for irreversible actions, CALM for secondary-important actions. Use `keyboards.confirm(..., danger=True)` for deletes.
- New FSM states go in `common.Flow`. New routers must be added to `build_router` in the right order; add to `LOCKED` if the whole router needs one cap.
- Admin roles (`telkap/services/roles.py`):
  - `.env` `ADMIN_IDS` users are always `owner`; others come from the `AdminRole` table.
  - Caps: `users`, `tickets`, `money`, `reports`, `system`. Role sets: `ROLE_CAPS` gives owner all caps, finance `{money, reports}`, support `{users, tickets}`.
  - API: `role_of`, `caps`, `can`, `can_cached` (sync, fail-closed), `is_staff`, `is_owner`, `set_role`, `remove`, `staff_ids(cap)`, `invalidate`.
- Code comments are Persian and often explain the reasons behind a design; read them before changing behaviour.
- CI: `pyflakes` + `ruff` + `pytest` with dummy env vars (`BOT_TOKEN`, `API_ID`, `API_HASH`, `ADMIN_IDS`, and a generated `FERNET_KEY`).