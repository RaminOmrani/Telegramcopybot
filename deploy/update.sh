#!/usr/bin/env bash
#
# بالا آوردن نسخه‌ی تازه روی سرور — با یک دستور.
#
#   sudo bash /opt/telkap/deploy/update.sh
#
# چهار کاری که این اسکریپت می‌کند و دستیِ آن‌ها فراموش می‌شود:
#
#   ۱. پیش از هر چیز از دیتابیس نسخه‌ی پشتیبان می‌گیرد. سشن‌های ورودِ
#      کاربران آنجاست؛ اگر برود، همه باید دوباره وارد شوند.
#   ۲. کامیت فعلی را یادداشت می‌کند و اگر نسخه‌ی تازه بالا نیامد،
#      خودش برمی‌گردد به همان و ربات را دوباره روشن می‌کند. یعنی
#      بدترین حالتِ یک به‌روزرسانی خراب، چند ثانیه قطعی است نه یک
#      شب بیداری.
#   ۳. کلیدهای تازه‌ی .env را گزارش می‌دهد.
#   ۴. مطمئن می‌شود سرویس واقعاً بالا آمده — نه اینکه فقط دستور
#      start خطا نداده باشد.
#
# .env هرگز دست نمی‌خورد. FERNET_KEY داخل آن است و بدون آن، سشن
# همه‌ی کاربران غیرقابل رمزگشایی می‌شود.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telkap}"
APP_USER="${APP_USER:-telkap}"
SERVICE="${SERVICE:-telkap}"
BRANCH="${BRANCH:-main}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "با sudo اجرا کنید: sudo bash $0"
cd "$APP_DIR" || die "پوشه‌ی $APP_DIR پیدا نشد"

# ── ۱) پشتیبان ──────────────────────────────────────────────────────
say "پشتیبان‌گیری از دیتابیس"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$APP_DIR/data/backups"
install -d -o "$APP_USER" -g "$APP_USER" "$BACKUP_DIR"

if [ -f "$APP_DIR/data/telkap.db" ]; then
    # ربات ممکن است همین حالا در حال نوشتن باشد. cp ساده می‌تواند
    # نسخه‌ی نیمه‌کاره بردارد؛ .backup خودِ sqlite نسخه‌ی سالم می‌دهد.
    if command -v sqlite3 >/dev/null 2>&1; then
        sudo -u "$APP_USER" sqlite3 "$APP_DIR/data/telkap.db" \
            ".backup '$BACKUP_DIR/telkap-$STAMP.db'"
    else
        warn "sqlite3 نصب نیست — کپی ساده گرفته می‌شود"
        sudo -u "$APP_USER" cp "$APP_DIR/data/telkap.db" \
            "$BACKUP_DIR/telkap-$STAMP.db"
    fi
    ok "data/backups/telkap-$STAMP.db"

    # فقط چند نسخه‌ی آخر بماند، وگرنه دیسک سرور پر می‌شود
    ls -1t "$BACKUP_DIR"/telkap-*.db 2>/dev/null \
        | tail -n +$((KEEP_BACKUPS + 1)) | xargs -r rm -f || true
else
    warn "هنوز دیتابیسی ساخته نشده — چیزی برای پشتیبان‌گیری نیست"
fi

# ── ۲) گرفتن نسخه‌ی تازه ────────────────────────────────────────────
OLD_COMMIT="$(sudo -u "$APP_USER" git rev-parse HEAD)"

say "گرفتن نسخه‌ی تازه از $BRANCH"
if ! sudo -u "$APP_USER" git diff --quiet; then
    die "روی سرور تغییر ذخیره‌نشده هست. اول تکلیفش را روشن کنید:
     git -C $APP_DIR status"
fi

sudo -u "$APP_USER" git fetch origin "$BRANCH"
NEW_COMMIT="$(sudo -u "$APP_USER" git rev-parse "origin/$BRANCH")"

if [ "$OLD_COMMIT" = "$NEW_COMMIT" ]; then
    ok "همین حالا آخرین نسخه است — کاری لازم نیست"
    systemctl is-active --quiet "$SERVICE" && ok "ربات در حال اجراست" \
        || warn "ربات خاموش است: systemctl start $SERVICE"
    exit 0
fi

printf '\n'
sudo -u "$APP_USER" git log --oneline "$OLD_COMMIT..$NEW_COMMIT" | sed 's/^/  /'

say "توقف ربات"
systemctl stop "$SERVICE"
ok "متوقف شد"

sudo -u "$APP_USER" git merge --ff-only "origin/$BRANCH"
ok "کد به‌روز شد → $(sudo -u "$APP_USER" git rev-parse --short HEAD)"

say "نصب کتابخانه‌های تازه"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r requirements.txt
ok "انجام شد"

# ── ۳) کلیدهای تازه‌ی .env ──────────────────────────────────────────
say "بررسی .env"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" tools/envsync.py || true

# ── ۴) روشن کردن و مطمئن شدن ────────────────────────────────────────
say "روشن کردن ربات"
# شمارنده‌ی راه‌اندازی مجدد صفر شود، وگرنه خرابیِ دفعه‌ی قبل به حساب
# این نسخه نوشته می‌شود
systemctl reset-failed "$SERVICE" || true
systemctl start "$SERVICE"

# سرویس ممکن است start شود و چند ثانیه بعد بیفتد. Restart=always هم
# آن را بارها بالا می‌آورد، پس «فعال بودن» در لحظه‌ی اول چیزی را ثابت
# نمی‌کند. این حلقه صبر می‌کند تا واقعاً روی پا بایستد.
for _ in $(seq 12); do
    sleep 1
    systemctl is-active --quiet "$SERVICE" || continue
    [ "$(systemctl show -p NRestarts --value "$SERVICE")" = "0" ] && break
done

if systemctl is-active --quiet "$SERVICE" \
   && [ "$(systemctl show -p NRestarts --value "$SERVICE")" = "0" ]; then
    printf '\n\033[32m✓ نسخه‌ی تازه بالا آمد.\033[0m\n'
    printf '  لاگ زنده:  journalctl -u %s -f\n' "$SERVICE"
    exit 0
fi

# ── برگشت ───────────────────────────────────────────────────────────
printf '\n\033[31m✗ ربات با نسخه‌ی تازه بالا نیامد. برمی‌گردم به نسخه‌ی قبلی.\033[0m\n'
journalctl -u "$SERVICE" -n 25 --no-pager | sed 's/^/  /'

systemctl stop "$SERVICE" || true
sudo -u "$APP_USER" git reset --hard "$OLD_COMMIT"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -q -r requirements.txt
systemctl start "$SERVICE"
sleep 3

if systemctl is-active --quiet "$SERVICE"; then
    printf '\n\033[33m! به نسخه‌ی قبلی برگشت و ربات دوباره بالا آمد.\033[0m\n'
    printf '  لاگ بالا را برای من بفرستید تا علت را پیدا کنم.\n'
else
    printf '\n\033[31m✗ نسخه‌ی قبلی هم بالا نیامد — یعنی مشکل از کد نیست.\033[0m\n'
    printf '  احتمالاً شبکه یا .env: journalctl -u %s -n 50\n' "$SERVICE"
fi
exit 1
