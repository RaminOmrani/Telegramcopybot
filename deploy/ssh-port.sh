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
        #
        # <b>هر دو خانواده صریحاً.</b> نوشتن «ListenStream=2222» یک سوکت
        # IPv6 می‌سازد که *معمولاً* IPv4 را هم می‌پذیرد — ولی این به
        # sysctl مربوط است و روی همه‌ی سرورها یکسان نیست. اگر آن سوکت
        # فقط IPv6 باشد، هر اتصال IPv4 به یک پورتِ عملاً بسته می‌خورد و
        # کرنل RST می‌فرستد: همان «Connection refused» که کل دنیا
        # می‌گیرد در حالی که ss نشان می‌دهد گوش می‌دهد.
        #
        # BindIPv6Only=ipv6-only لازم است: بدون آن سوکت IPv6 دوگانه
        # می‌شود و bind کردن 0.0.0.0 با خطای «آدرس در حال استفاده»
        # شکست می‌خورد.
        printf 'BindIPv6Only=ipv6-only\n'
        for port in 22 $PORTS; do
            printf 'ListenStream=0.0.0.0:%s\n' "$port"
            printf 'ListenStream=[::]:%s\n' "$port"
        done
    } > /etc/systemd/system/ssh.socket.d/port.conf
    systemctl daemon-reload
    systemctl restart ssh.socket
    ok "ssh.socket روی ۲۲ و $PORTS (هم IPv4 هم IPv6)"
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

# ── ۳) واقعاً وصل می‌شود؟ ────────────────────────────────────────────
#
# «ss نشان می‌دهد گوش می‌دهد» کافی نیست — دقیقاً همین اشتباه بود که
# باعث شد فکر کنیم کار تمام است در حالی که کل دنیا refused می‌گرفت.
# سوکتِ فقط-IPv6 در ss عادی به نظر می‌رسد. پس واقعاً وصل می‌شویم.
probe() {
    timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null
}

say "بررسی — اتصال واقعی، نه فقط فهرست ss"
sleep 1
printf '\n'
ss -tln | grep -E "[:.](22|$(echo "$PORTS" | tr ' ' '|'))\b" | sed 's/^/  /'
printf '\n'

listening=""
for port in 22 $PORTS; do
    if probe 127.0.0.1 "$port"; then
        ok "پورت $port — IPv4 ✓"
        [ "$port" = "22" ] || listening="$listening $port"
    elif probe ::1 "$port"; then
        warn "پورت $port فقط IPv6 است — از اینترنت IPv4 «refused» می‌گیرد"
    else
        warn "پورت $port اصلاً وصل نمی‌شود"
    fi
done

[ -n "$listening" ] || die "هیچ پورت تازه‌ای روی IPv4 بالا نیامد. ۲۲ سر جایش است.
     برای دیدن علت:  journalctl -u ssh --no-pager -n 30"

# ── ۴) چه چیزی را روی ویندوز بزنند ──────────────────────────────────
IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
IP="${IP:-<آی‌پی سرور>}"

printf '\n%s✓ تمام شد.%s\n\n' "$C_G" "$C_0"
printf '  حالا روی ویندوز، در cmd، این‌ها را یکی‌یکی امتحان کنید\n'
printf '  تا یکی جواب بدهد:\n\n'
for port in $listening; do
    printf '      %sssh -p %s root@%s%s\n' "$C_B" "$port" "$IP" "$C_0"
done
printf '\n  هرکدام که رمز پرسید، همان درست است — از این پس همیشه\n'
printf '  با همان -p وصل شوید.\n\n'
printf '  اگر باز هم جواب نداد، از بیرون بسنجید تا معلوم شود مشکل از\n'
printf '  مسیر ایران است یا از خودِ سرور:\n\n'
printf '      https://check-host.net/check-tcp?host=%s:%s\n\n' \
    "$IP" "$(set -- $listening; echo "$1")"
printf '  اگر «همه‌جای دنیا» رد شد، مشکل سمت سرور یا هاست است.\n'
printf '  اگر فقط نودهای ایران رد شدند، فیلترینگ مسیر است.\n\n'
