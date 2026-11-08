> Part of the AI handoff docs — start at [`AGENTS.md`](../../AGENTS.md).
> Written 2026-11-08 against `main` @ `42c14dd`. Code is the source of truth: if this file and the
> code disagree, trust the code and fix this file. "UNSURE" = not verified by running it.

# Business layer map: Telkap / فورواردبات


## 1. Plans and features: `plans.py`, `services/planstore.py`, `services/limits.py`

- **Feature flags** (`plans.py`): `FEAT_PUBLIC="public_source"`, `FEAT_PRIVATE="private_source"`, `FEAT_WATERMARK`, `FEAT_HISTORY`, `FEAT_VIP="vip_support"`. `FEAT_MESSAGES` is a quota, not an on/off flag.
- **`plans.py:Plan`** is a frozen dataclass with these fields: `code, title, days, price_toman, tagline, max_tasks, max_destinations` (the main destination counts), `period_messages, watermark_quota, history_quota, features, fair_use_daily`.
  - **Every quota covers the whole subscription period, not a day.**
  - `UNLIMITED = -1`. A value of `0` means the plan does not include that thing.
  - `Plan.quota(feature)` maps a feature to its numeric field.
  - `Plan.perks` builds the description text from the numbers.
- **Built-in plans** (`DEFAULT_PLANS`):

  | Code | Days | Price | Notes |
  |---|---|---|---|
  | `trial` | 1 | 0 | 20 msgs, public only |
  | `week` | 7 | 129k | |
  | `two_week` | 14 | 229k | private + watermark |
  | `month` | 30 | 429k | everything + VIP |
  | `custom` | 30 | 890k | unlimited msgs, `fair_use_daily=10_000` |

  - `_longer()` builds the long-term plans `month2/3/6/year` and `custom2/3/6/custom_year`. Quotas are multiplied by the number of months; unlimited stays unlimited. The tagline shows the discount percentage.
  - `PURCHASABLE_CODES` (first screen) = week, two_week, month, custom. `LONG_TERM_CODES` sit behind a "long" button (`plan:long`). `POPULAR_CODE = "custom3"`.
  - **Always read the live dict through `get_plan()`, `all_plans()` or `all_purchasable()`.** `PLANS` is the live dict and `DEFAULT_PLANS` is never changed.
- **Credit packs**:
  - `CREDIT_KINDS` = `wm`, `hist`, `ai` (`CREDIT_WATERMARK/HISTORY/AI`).
  - Default unit prices: 1000, 1000 and 500 toman. The live prices are in `CREDIT_UNITS`.
  - `credit_price(kind, qty)`. Suggested pack sizes: `CREDIT_PACKS = (50, 100, 500, 1000)`.
- **`services/planstore.py`** holds the admin's plan edits:
  - Edits are stored in the `PlanOverride` table, laid over the defaults (`apply_overrides`, `_rebuild`).
  - `load()` runs at startup and fills `plans.PLANS`.
  - `set_field`, `toggle_feature`, `reset` (deletes the override row), `set_credit_unit` (stored in `AppSetting` key `credit_unit`).
  - `FieldSpec.clean` checks and clamps values. A negative value means unlimited, and only for `quota`-kind fields.
  - `NUMERIC_FIELDS` can be overridden per user. `PLAN_ONLY_FIELDS` (price, days, title, tagline) are plan-level only.
- **`services/limits.py`** holds per-user overrides:
  - Stored as JSON in `users.limits`. Keys are field names, or `feat:<code>` set to True/False.
  - `apply(plan, limits)` returns a changed `Plan`. Also `set_value`, `set_feature`, `clear`, `clear_all`, `describe`.
  - Applied through `subscription.effective_plan`.

## 2. Entitlement and quotas: `services/entitlement.py`

- Usage counters live in `PeriodUsage(subscription_id, kind)`, so a renewal or new purchase starts again from zero. `_FEATURES` maps:
  - watermark → (`"watermark"`, `CREDIT_WATERMARK`)
  - history → (`"history"`, `CREDIT_HISTORY`)
  - messages → (`"messages"`, `None`). Messages cannot be bought as credits.
- **`Grant(feature, from_quota, from_credits, unlimited)`**, with `.total` and `.note`.
- **`reserve(user_id, feature, amount, plan, subscription_id)`**:
  - Takes from the plan quota first, then from purchased credits (`credits.consume`).
  - **All or nothing:** it returns `None` and deducts nothing if the total is not enough.
  - An unlimited plan gets `Grant(unlimited=True)`.
- **`release(user_id, grant, sub_id)`** puts the quota back (`_take` with a negative amount) and refunds the credits with `credits.add`. Callers must release when a send fails.
- `_take` handles the concurrent first-insert case (`IntegrityError`). Other functions: `affordable()` (checks without deducting), `quota_left()` (`BIG = 1<<30` stands in for unlimited), `used()`.
- **Where it is enforced:**
  - `services/copier.py`: around lines 1309–1394 (reserve/release around a send) and around line 2653 (message quota). The daily fair-use cap is checked around line 2629 against `DailyStat` and `daily_quota`.
  - `handlers/history.py` (copying old posts).
  - `services/diagnose.py` (read-only).
- AI credits do not go through entitlement: `services/aipass.py` calls `credits.consume(CREDIT_AI)` and refunds on failure.

## 3. Subscriptions and credits

- **`services/subscription.py`**:
  - `active_subscription` returns the row with the latest `expires_at` that is still in the future.
  - `effective_plan` / `active_plan_for` / `active_entitlement` return the plan with per-user limits applied (plus the subscription id for `active_entitlement`).
  - `grant(user_id, code, granted_by, note, replace)`:
    - With an active subscription, the new one is stacked after the current `expires_at`.
    - `replace=True` (an upgrade) ends the old subscription now.
    - It calls `cache.invalidate_user`.
  - `remaining_value` is the pro-rated toman value of the unused days, capped at `plan.days`.
  - `upgrade_quote` gives credit only if the new plan costs more than the current one, and never more than the new price.
  - `grant_trial_if_new` gives the trial only if the user has never had any subscription and `TRIAL_DAYS` > 0.
  - Admin tools: `adjust_days` (never moves expiry before now), `revoke`.
- **`services/credits.py`** stores balances in the columns `users.watermark_credits`, `history_credits`, `ai_credits`. Functions: `balance`, `balances`, `add`, `consume` (atomic). Credits never expire.

## 4. Money flows (the rules that matter)

**Core rule: money only comes in. Nothing is ever paid out to a bank account and there are no refunds.**

- The `WalletEntry.REASON_REFUND` entry is only used to reverse an internal wallet debit that failed to buy anything:
  - `services/reseller.py:activate`
  - `services/renewal.py:try_renew`
- Rewards and commissions go into the wallet only.

### `services/wallet.py`

- The balance is `users.wallet_toman`. Every change also writes a `WalletEntry` ledger row.
- `debit` is an atomic `UPDATE … WHERE wallet_toman >= amount`, so the balance cannot go negative.
- Other functions: `credit`, `adjust` (admin, ±), `history`.
- Reasons: referral, topup, purchase, refund, admin, commission.
- The customer's wallet balance is spent only by auto-renew. A reseller's wallet also pays for `reseller.activate`.
- **UNSURE:** I found no "pay for a plan from the wallet" option in `handlers/billing.py`. `METHODS` has no wallet entry.

### `services/payments.py`

- Methods: `METHOD_CARD="card"`, `METHOD_USDT="usdt"`, `METHOD_TRX="trx"`, `METHOD_GATEWAY="gate"` (ZarinPal). `CRYPTO_METHODS = (usdt, trx)`.
- **Zibal is not implemented**; ZarinPal is the only online gateway in code (`services/zarinpal.py`).
- The model is `PaymentRequest`:
  - `kind` is `KIND_PLAN`, `KIND_CREDIT` or `KIND_TOPUP`.
  - `status` is pending, approved or rejected.
  - It also stores `coupon_code`, `discount_toman`, `credit_toman`, `usdt_amount`, `tx_hash` and the receipt file.
- `quote()` builds the bill: list price minus upgrade credit, then the coupon is applied with `coupons.validate`. A bad coupon shows an error but does not block the purchase.
- Other functions:
  - Creating requests: `create_request`, `create_credit_request`, `create_topup` (`MIN_TOPUP_TOMAN = 50_000`, `MAX_TOPUP_TOMAN = 500_000_000`).
  - Attaching proof: `attach_receipt`, `attach_tx`, `attach_reference`.
  - Readiness: `any_method_ready()`, and `warn_no_method()` (alert with `CAP_MONEY`).
- **`approve(request_id, admin_id)`** works only on a request that is still pending, so the same receipt can never be approved twice. Then:
  - The coupon is redeemed only now (`coupons.redeem`).
  - Topup: the wallet is credited with `amount_toman`, the amount actually paid.
  - Credit purchase: `credits.add`.
  - Plan purchase: `grant(replace=credit_toman > 0)`.
  - Then, in this order, `referral.on_payment_approved` and, for plans only, `reseller.pay_commission`.
- `reject()` only changes the status.

### The four payment methods

1. **Card-to-card** (`services/cardinfo.py`):
   - The card number and holder are stored in `AppSetting` (`card_number`, `card_holder`). The panel value wins over the env vars `CARD_NUMBER` and `CARD_HOLDER`.
   - `normalize` requires 16 digits and accepts Persian or Arabic digits.
   - Flow: the user sends a receipt photo → `handlers/billing.py` forwards it to every `cfg.admin_ids` (around line 805; **owners from env only**) → admin presses `pay:ok:<id>` / `pay:no:<id>`, which needs `CAP_MONEY`.
2. **ZarinPal** (`services/zarinpal.py`, REST v4, `currency="IRT"`):
   - Merchant: `AppSetting` key `zarinpal_merchant`, falling back to env `ZARINPAL_MERCHANT`.
   - Functions: `start` (returns the StartPay URL), `verify` (codes 100 and 101 both count as success).
   - Callback is `web/server.py:zarinpal_return` at `/pay/zarinpal?rid=…`. It trusts no URL parameter: it verifies with the amount stored in the DB, then calls `payments.approve(admin_id=0)`. Needs `WEB_ENABLED` and `WEB_BASE_URL`.
3. **Crypto on TRON** (`services/coins.py`, `crypto.py`, `tron.py`, `cryptocheck.py`, `usdtrate.py`):
   - `coins.COINS` has USDT (TRC20; contract pinned as `USDT_CONTRACT` to block fake tokens; default margin 2%) and TRX (native; margin 5%). Both use the same wallet address (`AppSetting` key `usdt_address`).
   - `crypto.quote` locks the coin amount when the request is created.
   - `tron.incoming_usdt` / `incoming_trx` read the list of **our own wallet's incoming** transfers from TronGrid (optional `TRON_API_KEY`).
   - `cryptocheck.run_once` runs every 120 s and looks back 48 h. A request is approved automatically only if: the hash is in our incoming list; `enough()` holds (paid ≥ expected − `TOLERANCE` of 0.02); and `hash_already_used` is false (each hash once, checked in the DB). It approves with `SYSTEM_ADMIN_ID = -1`.
   - **The automatic check never rejects a payment.** Anything unresolved stays in the queue for an admin. `verify_now` is the manual re-check.
   - `usdtrate.py` sets the rate from Nobitex `USDTIRT` / `TRXIRT` (rial ÷ 10), with fallback to global sources. Guards: a sanity range, `MAX_JUMP_PERCENT = 25`, cross-checking several sources, and keeping the old rate when a fetch fails. The margin **lowers** the rate (`with_margin`). Auto mode is off by default (`usdt_rate_auto`).

### Discounts, rewards and sales

- **`services/coupons.py`**:
  - `validate` checks: enabled, expiry, `max_uses`, `plan_codes`, `min_toman`, `per_user_limit`.
  - Percent or fixed discount, never more than the amount.
  - `redeem` runs only at approval time. Also `create`, `toggle`, `remove`, `usage`.
- **`services/giftcodes.py`**:
  - A gift code is itself a plan, no payment needed. Codes are 10 characters from an alphabet without confusable characters; at most `MAX_BATCH = 200` per batch.
  - `redeem` is single-use, locked with `UPDATE … WHERE used_by IS NULL`. If the grant fails, the code is freed again.
- **`services/referral.py`**:
  - Settings are in `AppSetting` key `referral`. Defaults: enabled, percent mode, 15%, minimum purchase 100,000, no reward on credit purchases, not limited to the first purchase, no invitee discount. A per-referral cap and a 30-day cap exist but default to 0 (no cap).
  - `bind` happens once and cannot be changed; self-referral and two-way loops are refused.
  - The reward is paid **only on an approved purchase**, never at sign-up. `ReferralReward.payment_id` is unique, so one receipt pays at most once. Every non-payment is recorded as `STATUS_VOID` with a note.
- **`services/reseller.py`**:
  - Default discount 20%, maximum 90%. `discounted()` rounds down to the nearest 1000.
  - `activate` takes the money from the reseller's wallet first, then grants the plan to the customer; if the grant fails the money goes back. Resellers cannot sell to themselves, to banned users, or to users who never started the bot.
  - `claim` ties a customer to a reseller once (`users.owned_by`), only if `keeps_customers`.
  - `pay_commission` pays the discount difference into the reseller's wallet when the customer buys directly.
- **`services/renewal.py`**:
  - Auto-renew is opt-in and off by default (`toggle`). It runs within 24 h of expiry, every hour, and is paid from the wallet only.
  - If the balance is short, nothing partial is taken; the user gets one notice (`KIND_SHORT`).
- **`services/reminders.py`**:
  - Expiry reminders at 3 and 1 days left, recorded in `ReminderState`.
  - An optional win-back coupon rides along (`AppSetting` key `winback_coupon`).
  - `ask_churn` asks why the user left, 6–96 h after expiry; `handlers/churn.py` and `analytics.record_churn` store the answer.

## 5. Terms: `services/terms.py`

- Has a `VERSION` (=1). `accepted()` requires `users.terms_version >= VERSION`, so raising `VERSION` makes everyone accept again. `accept()` stores the version and the time.
- The single gate is at account connection (login).
- Key clauses:
  - Content responsibility is the user's.
  - Account-ban risk is the user's.
  - **No refund, neither cash nor to the wallet.** A ban for prohibited content also comes with no refund.

## 6. Operations

- **`services/pool.py`** is the pool of service accounts that read public sources in simple mode (no customer account).
  - `lease` keeps a sticky `SourceLease` and picks the least-loaded healthy account, with `SOURCES_PER_ACCOUNT = 120`. Sources are moved away from banned or disabled accounts.
  - Also `release`, `mark_flood`, `mark_banned` (an account never comes back on its own), `revive`, `capacity`, and `NoAccount`, which raises loudly.
  - Admin login happens by QR code in `handlers/admin_pool.py` via `svclogin`.
- **`services/forcejoin.py`**: forced-join channels in `ForceJoinChannel` (60 s cache), seeded from `FORCE_JOIN_CHANNEL`. Enforced in `middlewares.py` around line 155; env admins skip it.
- **`services/support.py`**: anonymous tickets (`SupportTicket` / `SupportMessage`). Admin replies arrive signed as "support". `MAX_TEXT = 3000`.
- **`services/analytics.py`**: `revenue` is the sum of approved `PaymentRequest` amounts (plan and credit) plus `ResellerSale`. Also `funnel`, `retention`, `dashboard`, `churn_summary`.
- **`services/digest.py`**: a daily per-user summary at 09:00 local time, opt-in.
- **`services/alerts.py`**: `send(text, cap=, key=, cooldown=)` goes to `roles.staff_ids(cap)`. The watchdog runs every 15 min and checks the retry queue (> 200), sick accounts, and stale receipts (> 12 h, ≥ 3).
- **`services/backup.py`**: SQLite copy every `BACKUP_HOURS` (12), keeps 14, compresses and sends to `BACKUP_CHAT_ID` (`AppSetting` key `backup_chat_id`). Bot API upload limit is 50 MB.
- **`services/maintenance.py`**: `maintenance_mode` (staff skip it, see `middlewares.py`). Prunes the activity log every 6 h (`LOG_RETENTION_DAYS`) and old `ReminderState` rows.
- **Background loops** start in `main.py` around lines 205–244: reminders, digest, backup, renewal, maintenance, alerts, cryptocheck, usdtrate, among others.

## 7. Admins and the admin panel

- **Configuration:**
  - `ADMIN_IDS` (comma-separated, read in `config.py:Settings.admin_ids` / `is_admin`) are the **owners**. They cannot be removed and have all capabilities.
  - Extra staff are stored in `AdminRole` (`services/roles.py`):

    | Role | Capabilities |
    |---|---|
    | `ROLE_OWNER` | all |
    | `ROLE_FINANCE` | `CAP_MONEY` + `CAP_REPORTS` |
    | `ROLE_SUPPORT` | `CAP_USERS` + `CAP_TICKETS` |

  - Checks use `roles.can`, `is_staff`, `can_cached`.
  - **Gotcha:** card receipts (`billing.py` around line 805) and new tickets (`handlers/support.py` around line 159) go to the env `admin_ids` only, not to finance or support staff.
  - Other related env vars: `SUPPORT_USERNAME`, `TRIAL_DAYS`, `CARD_*`, `ZARINPAL_MERCHANT`, `TRON_API_KEY`, `BACKUP_*`, `WEB_*`, `FORCE_JOIN_CHANNEL`.
- **Panel handlers** (`/admin` command, callbacks `adm:*`):
  - `handlers/admin.py`: home screen filtered by capability, `/stats`, pending payments (`adm:pay`), broadcast (`adm:cast`), `/grant`, `/ban`, `/unban`, backup, retry, `/chatid`.
  - `admin_users.py` (`admu:*`): find a user, give a plan, ±days, revoke, ban/unban, DM, adjust wallet (`admu:wal`), give watermark/history credits (`admu:cwm`, `admu:chist`), set reseller (`admu:res`), view tasks.
  - `admin_limits.py` (`ul:*`): per-user limits.
  - `admin_plans.py` (`pe:*`): edit plan fields and features, reset, credit unit prices.
  - `admin_coupons.py` (`cp:*`, `gf:*`): coupons, win-back coupon, gift-code batches.
  - `admin_referral.py` (`rf:set:*`): edits the fields in `referral.FIELDS`.
  - `admin_reports.py` (`rep:*`): money, funnel, churn, audit log.
  - `admin_channels.py` (`fj:*`): forced-join channels.
  - `admin_pool.py` (`pool:*`): service accounts.
  - `admin_system.py` (`sys:*`): roles, maintenance mode, card number and holder, USDT address and rate, auto rate and margin, ZarinPal, web panel password and session, AI settings, backup chat id.
- There is also a web admin panel at `web/server.py` (for example `/settings/zarinpal`).

## 8. Business rules locked by tests

- **`tests/test_terms.py`**:
  - The text contains "بازگردانده نمی‌شود" (non-refundable) and "بدون بازگرداندن وجه" (without refund), and never "عودت وجه" (refund).
  - Login is gated on accepting the terms; accepting an old version does not count.
  - Landing-page checks on `site/index.html`:
    - Prices must match `plans.py`. `tools/site.py --check` must pass, so regenerate the page after any price change.
    - No refund promises anywhere on the page.
    - The `#agents` (reseller) section must not promise any payout ("واریز به حساب", "برداشت وجه", "تسویه نقدی").
- **`tests/test_v7.py`**: the wallet never goes negative and keeps a ledger; the referrer binds once; a reward is paid only after approval; caps are respected; the same receipt never pays twice; reseller rules; a customer cannot be taken from their reseller; a former reseller stops earning.
- **`tests/test_v9.py`**:
  - Coupons: every rule is enforced, a coupon is spent only on approval, the discount never exceeds the price.
  - Upgrades: credit applies only to a pricier plan, the new plan starts now and replaces the old one.
  - Gift codes are single-use; auto-renew is opt-in and takes nothing partial.
- **`tests/test_v4.py`**: `adjust_days` never goes below now; revoke stops copying.
- **`tests/test_topup.py`**: the topup range is enforced and a topup is never approved twice. Long-term plans are cheaper per month, their discount grows with the term, and their quotas grow with it.
- **`tests/test_cryptocheck.py`, `tests/test_trx.py`**: fake USDT is refused; tolerance is 0.02; a hash works once; a network failure approves nothing; USDT and TRX are never confused; changing the address or rate alerts the admins.
- **`tests/test_paysettings.py`, `tests/test_zarinpal.py`**: the margin lowers the rate, never raises it; wild jumps are refused; the panel value beats the env value; ZarinPal is verified with the amount we hold, not the one in the URL.
- **`tests/test_pool.py`**: sticky leases; banned accounts never come back on their own.