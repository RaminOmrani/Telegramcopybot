#!/usr/bin/env bash
#
# بردن SSH روی یک پورت دیگر — برای دور زدن فیلترینگِ پورت ۲۲.
#
#   bash /opt/telkap/deploy/ssh-port.sh          # پیش‌فرض: ۲۲۲۲
#   bash /opt/telkap/deploy/ssh-port.sh 8443     # یا هر پورت دیگر
#
# <b>چرا اصلاً لازم است.</b> فیلترینگ ایران معمولاً پورت ۲۲ را
# می‌شناسد و اتصال را وسط دست‌دادن قطع می‌کند. سرور سالم است و sshd
# هم کار می‌کند؛ فقط مسیرِ رسیدن به آن بسته است. پورت تازه در بیشتر
# موارد از این تشخیص رد می‌شود.
#
# <b>پورت ۲۲ عمداً باز می‌ماند.</b> اگر پورت تازه جواب ندهد، راه
# برگشت از کنسول لازم نباشد — همان ۲۲ سر جایش است.
#
# <b>اوبونتو ۲۴ فرق دارد.</b> برخلاف نسخه‌های قبلی، پورت را از
# sshd_config نمی‌گیرد؛ سرویس با socket activation بالا می‌آید و پورت
# آنجا تعیین می‌شود. اسکریپت خودش می‌فهمد کدام حالت است.

set -euo pipefail

PORT="${1:-2222}"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "با root اجرا کنید"
case "$PORT" in
    ''|*[!0-9]*) die "پورت باید عدد باشد: bash $0 2222" ;;
esac
[ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "پورت را بین ۱۰۲۴ تا ۶۵۵۳۵ بگذارید"
[ "$PORT" = "22" ] && die "۲۲ همان پورتی است که فیلتر می‌شود"

# ── ۱) فایروال، پیش از هر چیز ───────────────────────────────────────
# اگر sshd روی پورتی گوش بدهد که فایروال بسته نگه داشته، نتیجه‌اش
# «کار نمی‌کند» است بدون هیچ سرنخی. پس اول باز می‌شود.
say "باز کردن پورت $PORT در فایروال"
if command -v ufw >/dev/null 2>&1; then
    ufw allow "$PORT/tcp" >/dev/null 2>&1 || true
    ufw allow 22/tcp >/dev/null 2>&1 || true       # راه برگشت
    ok "ufw: پورت $PORT و ۲۲ باز شدند"
else
    warn "ufw نصب نیست — فایروال دیگری در کار است یا هیچ‌کدام"
fi

# ── ۲) تعیین پورت ───────────────────────────────────────────────────
if systemctl list-unit-files ssh.socket >/dev/null 2>&1 \
   && systemctl is-enabled ssh.socket >/dev/null 2>&1; then
    say "پیکربندی ssh.socket (روش اوبونتو ۲۴)"
    mkdir -p /etc/systemd/system/ssh.socket.d
    cat > /etc/systemd/system/ssh.socket.d/port.conf <<EOF
[Socket]
# خالی گذاشتن، فهرست پیش‌فرض را پاک می‌کند؛ بدون آن پورت‌ها اضافه
# می‌شوند نه جایگزین، و ترتیبشان قابل پیش‌بینی نیست.
ListenStream=
ListenStream=22
ListenStream=$PORT
EOF
    systemctl daemon-reload
    systemctl restart ssh.socket
    ok "ssh.socket روی ۲۲ و $PORT"
else
    say "پیکربندی sshd_config (روش کلاسیک)"
    mkdir -p /etc/ssh/sshd_config.d
    printf 'Port 22\nPort %s\n' "$PORT" > /etc/ssh/sshd_config.d/99-port.conf
    sshd -t || die "پیکربندی sshd ایراد دارد؛ چیزی عوض نشد"
    systemctl restart ssh 2>/dev/null || systemctl restart sshd
    ok "sshd روی ۲۲ و $PORT"
fi

# ── ۳) واقعاً گوش می‌دهد؟ ────────────────────────────────────────────
say "بررسی"
sleep 1
if ss -tln | grep -qE "[:.]$PORT\b"; then
    ok "روی پورت $PORT گوش می‌دهد"
else
    die "پورت $PORT بالا نیامد. ۲۲ هنوز کار می‌کند، پس چیزی از دست نرفته.
     برای دیدن علت:  journalctl -u ssh -n 30 --no-pager"
fi
ss -tln | grep -E '[:.](22|'"$PORT"')\b' | sed 's/^/  /'

# ── ۴) چه چیزی را روی ویندوز بزنند ──────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
printf '\n\033[32m✓ تمام شد.\033[0m\n\n'
printf '  حالا روی ویندوز، در cmd:\n\n'
printf '      \033[1mssh -p %s root@%s\033[0m\n\n' "$PORT" "${IP:-<آی‌پی سرور>}"
printf '  اگر جواب داد، از این پس همیشه با -p %s وصل شوید.\n' "$PORT"
printf '  اگر نداد، پورت دیگری را امتحان کنید:  bash %s 8443\n' "$0"
printf '  پورت ۲۲ باز مانده، پس راه برگشت همیشه هست.\n\n'
