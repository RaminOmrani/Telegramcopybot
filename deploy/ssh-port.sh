#!/usr/bin/env bash
#
# بردن SSH روی پورت‌های دیگر — برای دور زدن فیلترینگِ پورت ۲۲.
#
#   bash /opt/telkap/deploy/ssh-port.sh                # پیش‌فرض: چند پورتِ محتمل
#   bash /opt/telkap/deploy/ssh-port.sh 8443           # فقط یکی
#   bash /opt/telkap/deploy/ssh-port.sh 8443 2083 2222 # چندتا با هم
#
# <b>چرا چند پورت با هم.</b> هر بار برگشتن به کنسول VNC برای امتحان
# یک پورت تازه، وقتِ تلف‌شده است. همه با هم باز می‌شوند و از ویندوز
# یکی‌یکی امتحان می‌شوند — یک رفت‌وبرگشت به‌جای پنج تا.
#
# <b>کدام پورت‌ها و چرا.</b> ترتیب پیش‌فرض از روی چیزی است که در عمل
# جواب می‌دهد: پورت‌هایی که شبیه HTTPS‌اند کمتر بسته می‌شوند، چون
# بستنشان ترافیک عادی وب را هم می‌شکند.
#
# <b>پورت ۲۲ همیشه باز می‌ماند.</b> اگر هیچ‌کدام جواب ندهند، راه
# برگشت از کنسول لازم نیست — همان ۲۲ سر جایش است.
#
# <b>اوبونتو ۲۴ فرق دارد.</b> برخلاف نسخه‌های قبلی، پورت را از
# sshd_config نمی‌گیرد؛ سرویس با socket activation بالا می‌آید و پورت
# آنجا تعیین می‌شود. اسکریپت خودش می‌فهمد کدام حالت است.

set -euo pipefail

# ۸۴۴۳ و ۲۰۸۳ و ۲۰۸۷ پورت‌های شناخته‌شده‌ی پنل‌های وب‌اند و ترافیکشان
# عادی به نظر می‌رسد. ۲۲۲۲ آخر است چون همان است که «refused» گرفت.
DEFAULT_PORTS="8443 2083 2087 2222"

say()  { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "با root اجرا کنید"

PORTS="${*:-$DEFAULT_PORTS}"
for port in $PORTS; do
    case "$port" in
        ''|*[!0-9]*) die "«$port» عدد نیست" ;;
    esac
    [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] \
        || die "پورت $port خارج از بازه‌ی ۱۰۲۴ تا ۶۵۵۳۵ است"
    [ "$port" = "22" ] && die "۲۲ خودش همیشه باز می‌ماند؛ لازم نیست بنویسیدش"
done

# ── ۱) فایروال، پیش از هر چیز ───────────────────────────────────────
# اگر sshd روی پورتی گوش بدهد که فایروال بسته نگه داشته، نتیجه‌اش
# «کار نمی‌کند» است بدون هیچ سرنخی. پس اول باز می‌شود.
say "باز کردن پورت‌ها در فایروال"
if command -v ufw >/dev/null 2>&1; then
    ufw allow 22/tcp >/dev/null 2>&1 || true       # راه برگشت
    for port in $PORTS; do
        ufw allow "$port/tcp" >/dev/null 2>&1 || true
    done
    ok "ufw: ۲۲ و $PORTS"
else
    warn "ufw نصب نیست — فایروال دیگری در کار است یا هیچ‌کدام"
fi

# ── ۲) تعیین پورت ───────────────────────────────────────────────────
if systemctl list-unit-files ssh.socket >/dev/null 2>&1 \
   && systemctl is-enabled ssh.socket >/dev/null 2>&1; then
    say "پیکربندی ssh.socket (روش اوبونتو ۲۴)"
    mkdir -p /etc/systemd/system/ssh.socket.d
    {
        printf '[Socket]\n'
        # خالی گذاشتن، فهرست پیش‌فرض را پاک می‌کند؛ بدون آن پورت‌ها
        # اضافه می‌شوند نه جایگزین، و اجرای دوباره‌ی اسکریپت پورت‌های
        # قبلی را هم نگه می‌داشت.
        printf 'ListenStream=\n'
        printf 'ListenStream=22\n'
        for port in $PORTS; do
            printf 'ListenStream=%s\n' "$port"
        done
    } > /etc/systemd/system/ssh.socket.d/port.conf
    systemctl daemon-reload
    systemctl restart ssh.socket
    ok "ssh.socket روی ۲۲ و $PORTS"
else
    say "پیکربندی sshd_config (روش کلاسیک)"
    mkdir -p /etc/ssh/sshd_config.d
    {
        printf 'Port 22\n'
        for port in $PORTS; do
            printf 'Port %s\n' "$port"
        done
    } > /etc/ssh/sshd_config.d/99-port.conf
    sshd -t || die "پیکربندی sshd ایراد دارد؛ چیزی عوض نشد"
    systemctl restart ssh 2>/dev/null || systemctl restart sshd
    ok "sshd روی ۲۲ و $PORTS"
fi

# ── ۳) واقعاً گوش می‌دهند؟ ───────────────────────────────────────────
say "بررسی"
sleep 1
listening=""
for port in $PORTS; do
    if ss -tln | grep -qE "[:.]$port\b"; then
        listening="$listening $port"
    else
        warn "پورت $port بالا نیامد"
    fi
done
[ -n "$listening" ] || die "هیچ پورتی بالا نیامد. ۲۲ هنوز کار می‌کند، پس چیزی از دست نرفته.
     برای دیدن علت:  journalctl -u ssh --no-pager -n 30"
ok "گوش می‌دهد روی:$listening"

# ── ۴) چه چیزی را روی ویندوز بزنند ──────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
IP="${IP:-<آی‌پی سرور>}"

printf '\n\033[32m✓ تمام شد.\033[0m\n\n'
printf '  حالا روی ویندوز، در cmd، این‌ها را یکی‌یکی امتحان کنید\n'
printf '  تا یکی جواب بدهد:\n\n'
for port in $listening; do
    printf '      \033[1mssh -p %s root@%s\033[0m\n' "$port" "$IP"
done
printf '\n  هرکدام که رمز پرسید، همان درست است — از این پس همیشه\n'
printf '  با همان -p وصل شوید.\n\n'
printf '  اگر همه «Connection refused» دادند، یعنی فایروالِ شبکه‌ی\n'
printf '  هاست جلوی پورت‌های تازه را گرفته: در پنل ParsPack بخش\n'
printf '  Firewall را ببینید.\n'
printf '  اگر همه «timed out» دادند، فیلترینگ روی خودِ پروتکل SSH\n'
printf '  است نه پورت — آن‌وقت با وی‌پی‌ان امتحان کنید.\n\n'
