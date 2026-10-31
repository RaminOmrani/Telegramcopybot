#!/usr/bin/env bash
#
# بستن ورود با رمز، و جمع کردن پورت‌های اضافی.
#
#   bash /opt/telkap/deploy/ssh-harden.sh 8443
#
# <b>فقط بعد از اینکه «ssh bot» بدون پرسیدن رمز وارد شد اجرا کنید.</b>
# اگر کلید کار نکند و رمز هم بسته شود، تنها راه باقی‌مانده کنسول
# VNC است.
#
# <b>چرا این کار لازم است.</b> ورود root با رمز، روی پورتی که در
# اینترنت باز است، هدف حمله‌ی خودکار است — رباتی که شبانه‌روز رمز
# امتحان می‌کند. با کلید، حدس زدن عملاً ناممکن می‌شود.
#
# <b>چرا خطر قفل شدن ندارد.</b> این فقط ورود <b>از راه SSH</b> با
# رمز را می‌بندد. کنسول VNC پنل هاست یک ترمینال محلی است و از SSH رد
# نمی‌شود — پس همیشه با همان رمز root باز می‌شود. برای برگرداندن:
#
#   rm /etc/ssh/sshd_config.d/98-harden.conf && systemctl restart ssh

set -euo pipefail

# رنگ فقط وقتی خروجی به یک ترمینال واقعی می‌رود. اجرای از راه دور
# مثل «ssh bot "bash ..."» ترمینال ندارد، و cmd ویندوز کدهای رنگ را
# نمی‌فهمد — پس خام چاپشان می‌کند و خروجی پر از «[32m» می‌شود.
if [ -t 1 ]; then
    C_B=$'\033[1m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_0=$'\033[0m'
else
    C_B=''; C_G=''; C_Y=''; C_R=''; C_0=''
fi

say()  { printf '\n%s▸ %s%s\n' "$C_B" "$*" "$C_0"; }
ok()   { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_Y" "$C_0" "$*"; }
die()  { printf '\n%s✗ %s%s\n' "$C_R" "$*" "$C_0" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "با root اجرا کنید"

KEEP="${1:-8443}"
case "$KEEP" in
    ''|*[!0-9]*) die "پورت را بدهید: bash $0 8443" ;;
esac

# ── ۱) کلید واقعاً هست؟ ─────────────────────────────────────────────
# مهم‌ترین محافظ این اسکریپت. بستن رمز بدون داشتن کلید یعنی بستن در
# به روی خودتان.
say "بررسی کلید"
KEYS="/root/.ssh/authorized_keys"
if [ ! -s "$KEYS" ]; then
    die "هیچ کلیدی در $KEYS نیست.
     اول از ویندوز کلید را بفرستید، «ssh bot» را تست کنید، بعد اینجا برگردید."
fi
count="$(grep -cE '^(ssh|ecdsa)-' "$KEYS" || true)"
[ "${count:-0}" -ge 1 ] || die "فایل کلید هست ولی هیچ کلید معتبری داخلش نیست."
ok "$count کلید پیدا شد"
sed 's/^\(.\{0,50\}\).*/  \1…/' "$KEYS"

# ── ۲) بستن ورود با رمز ─────────────────────────────────────────────
say "بستن ورود با رمز"
mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/98-harden.conf <<'EOF'
# ورود فقط با کلید. رمز دیگر پذیرفته نمی‌شود.
PasswordAuthentication no
# این یکی جدا لازم است: بعضی توزیع‌ها رمز را از این مسیر هم می‌پذیرند
# و بستن فقط PasswordAuthentication در عمل چیزی را نمی‌بندد.
KbdInteractiveAuthentication no
# root می‌تواند وارد شود، ولی فقط با کلید
PermitRootLogin prohibit-password
EOF
sshd -t || die "پیکربندی ایراد دارد؛ چیزی عوض نشد"
ok "PasswordAuthentication no"

# ── ۳) جمع کردن پورت‌ها ─────────────────────────────────────────────
say "نگه داشتن پورت $KEEP و ۲۲، بستن بقیه"

# ۲۲ عمداً می‌ماند: هزینه‌اش صفر است (ورود با رمز که بسته شد) و اگر
# روزی پورت دیگری از کار بیفتد، یک راه باز باقی می‌ماند.
if systemctl list-unit-files ssh.socket >/dev/null 2>&1 \
   && systemctl is-enabled ssh.socket >/dev/null 2>&1; then
    mkdir -p /etc/systemd/system/ssh.socket.d
    {
        printf '[Socket]\n'
        printf 'ListenStream=\n'
        printf 'BindIPv6Only=ipv6-only\n'
        for port in 22 "$KEEP"; do
            printf 'ListenStream=0.0.0.0:%s\n' "$port"
            printf 'ListenStream=[::]:%s\n' "$port"
        done
    } > /etc/systemd/system/ssh.socket.d/port.conf
    systemctl daemon-reload
    systemctl restart ssh.socket
else
    printf 'Port 22\nPort %s\n' "$KEEP" > /etc/ssh/sshd_config.d/99-port.conf
    sshd -t || die "پیکربندی ایراد دارد"
fi

systemctl restart ssh 2>/dev/null || systemctl restart sshd || true

if command -v ufw >/dev/null 2>&1; then
    for port in 2222 2083 2087 8443; do
        [ "$port" = "$KEEP" ] && continue
        ufw delete allow "$port/tcp" >/dev/null 2>&1 || true
    done
fi
ok "فقط ۲۲ و $KEEP باز ماندند"

# ── ۴) بررسی ────────────────────────────────────────────────────────
say "بررسی"
sleep 1
ss -tln | grep -E '[:.](22|'"$KEEP"')\b' | sed 's/^/  /'

printf '\n%s✓ تمام شد.%s\n\n' "$C_G" "$C_0"
printf '  «این پنجره را نبندید» تا در یک پنجره‌ی تازه امتحان کنید:\n\n'
printf '      %sssh bot%s\n\n' "$C_B" "$C_0"
printf '  اگر بدون رمز وارد شد، کار تمام است.\n'
printf '  اگر نشد، از همین پنجره برگردانید:\n\n'
printf '      rm /etc/ssh/sshd_config.d/98-harden.conf\n'
printf '      systemctl restart ssh\n\n'
printf '  و اگر این پنجره را هم بستید، کنسول VNC پنل هاست همیشه با\n'
printf '  رمز root باز می‌شود — از SSH رد نمی‌شود، پس قفل نمی‌شوید.\n\n'
