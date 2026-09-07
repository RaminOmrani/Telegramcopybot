#!/usr/bin/env bash
#
# nginx و گواهی SSL برای یک زیردامنه — با یک دستور.
#
#   sudo bash /opt/telkap/deploy/web-setup.sh forwardbot.softmiliac.com you@email.com
#
# و اگر نام دیگری هم به همین‌جا اشاره می‌کند، بعد از ایمیل بیاورید؛
# روی همان گواهی می‌نشیند و به نامِ اصلی تغییرمسیر می‌خورد:
#
#   sudo bash ... forwardbot.softmiliac.com you@email.com botpanel.softmiliac.com
#
# یک زیردامنه، سه چیز:
#
#   /        صفحه‌ی فروش — فایل ثابت از پوشه‌ی site/
#   /panel   پنل مدیریت — به ربات پراکسی می‌شود
#   /app     مینی‌اپ تلگرام — فایل ثابت از site/app/
#
# چرا یکی و نه سه زیردامنه. هر زیردامنه یک رکورد DNS، یک گواهی و یک
# جای دیگر برای خراب شدن است؛ و دامنه‌ی اصلی مالِ سایت شرکت است. با
# یک نام، همه‌ی این‌ها یک گواهی و یک فایل پیکربندی دارند.
#
# چرا nginx لازم است. پنل داخل خودِ پروسه‌ی ربات بالا می‌آید و روی
# 127.0.0.1 گوش می‌دهد، یعنی فقط از خودِ سرور در دسترس است. nginx تنها
# چیزی است که از بیرون دیده می‌شود: HTTPS را می‌رساند، فایل‌های ثابت را
# خودش می‌دهد، و بقیه را به ربات می‌سپارد.
#
# چرا HTTPS اختیاری نیست. کوکی ورود به پنل با پرچم Secure فرستاده
# می‌شود، پس روی http اصلاً برنمی‌گردد و ورود در حلقه می‌افتد. مهم‌تر
# از آن: لینک ورود و نشست ادمین روی http لخت روی شبکه می‌روند و هرکس
# وسط راه باشد می‌تواند وارد پنل شود.
#
# ترتیب کارها عمدی است. اول .env و ربات، بعد nginx، آخر گواهی — تا
# اگر جایی خطا داد، دقیقاً بدانید کدام حلقه شکسته، نه اینکه ته کار با
# یک «۵۰۲ Bad Gateway» بی‌نشانی روبه‌رو شوید.
#
# .env فقط سه کلید مربوط به وب را می‌گیرد و اول از آن نسخه‌ی پشتیبان
# برداشته می‌شود. FERNET_KEY و توکن‌ها دست نمی‌خورند.

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telkap}"
ENV_FILE="$APP_DIR/.env"
SERVICE="${SERVICE:-telkap}"
WEB_PORT="${WEB_PORT:-8080}"

PANEL_PATH="${PANEL_PATH:-/panel}"     # باید با render.PREFIX یکی باشد
SITE_DIR="${SITE_DIR:-$APP_DIR/site}"

DOMAIN="${1:-}"
EMAIL="${2:-}"
# آرگومان سوم به بعد، نام‌های دیگری که به همین‌جا می‌آیند. shift فقط
# وقتی که واقعاً آن‌قدر آرگومان هست؛ وگرنه با set -u می‌ایستد و — بدتر —
# با «|| true» نمی‌ایستد ولی $@ دست‌نخورده می‌ماند و خودِ دامنه به
# فهرست نام‌های دیگر می‌رود.
ALIASES=()
if [ "$#" -gt 2 ]; then
    shift 2
    ALIASES=("$@")
fi

# رنگ فقط وقتی خروجی به ترمینال می‌رود؛ در لاگ و فایل، کدهای رنگ
# فقط آشغال اضافه‌اند
if [ -t 1 ]; then
    R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; B=$'\033[1m'; N=$'\033[0m'
else
    R=""; G=""; Y=""; B=""; N=""
fi

say()  { printf '%s\n' "$*"; }
ok()   { printf '%s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s!%s %s\n' "$Y" "$N" "$*"; }
die()  { printf '%s✗ %s%s\n' "$R" "$*" "$N" >&2; exit 1; }
step() { printf '\n%s── %s%s\n' "$B" "$*" "$N"; }

# ── بررسی‌های پیش از دست زدن به چیزی ─────────────────────────────────

[ "$(id -u)" -eq 0 ] || die "این اسکریپت را با sudo اجرا کنید."

if [ -z "$DOMAIN" ]; then
    die "دامنه را بدهید:
    sudo bash $0 forwardbot.softmiliac.com you@email.com"
fi

for name in "$DOMAIN" "${ALIASES[@]}"; do
    case "$name" in
        *.*) : ;;
        *) die "«$name» دامنه به نظر نمی‌رسد." ;;
    esac
done

[ -f "$ENV_FILE" ] || die "فایل $ENV_FILE پیدا نشد. مسیر نصب درست است؟"

step "بررسی DNS"

# اگر دامنه به این سرور اشاره نکند، certbot با خطایی شکست می‌خورد که
# علتش را نمی‌گوید. اینجا زودتر و با پیام روشن گرفته می‌شود.
#
# IP سرور از روی خودِ کارت شبکه خوانده می‌شود نه از یک سرویس بیرونی،
# چون سرویس بیرونی ممکن است از این شبکه در دسترس نباشد و بررسی را
# بی‌دلیل خراب کند.
mine="$(ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1)"

for name in "$DOMAIN" "${ALIASES[@]}"; do
    resolved="$(getent ahostsv4 "$name" 2>/dev/null | awk '{print $1; exit}' || true)"

    if [ -z "$resolved" ]; then
        die "«$name» به هیچ IP اشاره نمی‌کند.
    رکورد A را در Zone Editor پنل دامنه بسازید و چند دقیقه صبر کنید."
    fi

    if printf '%s\n' $mine | grep -qx "$resolved"; then
        ok "$name → $resolved (همین سرور)"
    else
        warn "«$name» به $resolved اشاره می‌کند، ولی IP این سرور اینهاست:"
        printf '%s\n' $mine | sed 's/^/    /'
        warn "اگر پشت CDN یا فایروال ابری هستید طبیعی است؛ وگرنه گواهی گرفته نمی‌شود."
    fi
done

step "نصب nginx و certbot"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx curl >/dev/null
ok "نصب شد"

# ── .env ─────────────────────────────────────────────────────────────

step "تنظیم .env"

cp -a "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"

# جای یک کلید را عوض می‌کند، یا اگر نبود اضافه‌اش می‌کند. با awk نوشته
# شده نه sed، چون مقدارها می‌توانند / و & داشته باشند و در sed معنای
# دیگری می‌دهند.
set_env() {
    local key="$1" value="$2" tmp
    tmp="$(mktemp)"
    KEY="$key" VALUE="$value" awk '
        BEGIN { key = ENVIRON["KEY"]; value = ENVIRON["VALUE"]; done = 0 }
        $0 ~ "^" key "=" { print key "=" value; done = 1; next }
        { print }
        END { if (!done) print key "=" value }
    ' "$ENV_FILE" > "$tmp"
    cat "$tmp" > "$ENV_FILE"     # جای mv، تا مالک و دسترسی فایل عوض نشود
    rm -f "$tmp"
}

set_env WEB_ENABLED true
set_env WEB_HOST 127.0.0.1
set_env WEB_PORT "$WEB_PORT"
# پیشوند حتماً داخل WEB_BASE_URL می‌آید. لینکِ ورودی که ربات می‌فرستد و
# نشانی بازگشتِ زرین‌پال هر دو از همین ساخته می‌شوند؛ بدون پیشوند، هر دو
# روی صفحه‌ی فروش می‌افتند. ربات موقع بالا آمدن هم این را می‌سنجد و اگر
# نخواند در لاگ هشدار می‌دهد.
set_env WEB_BASE_URL "https://$DOMAIN$PANEL_PATH"
ok "WEB_ENABLED، WEB_HOST، WEB_PORT و WEB_BASE_URL نوشته شدند"
say "  نسخه‌ی پشتیبان: $ENV_FILE.bak-*"

# ── ربات ─────────────────────────────────────────────────────────────

step "راه‌اندازی دوباره‌ی ربات"

systemctl restart "$SERVICE"

# پیش از nginx بررسی می‌شود که پنل واقعاً بالا آمده. اگر این را جا
# بیندازیم، خطای بعدی یک «502 Bad Gateway» است که نمی‌گوید تقصیر
# کیست.
upstream_ok=0
for _ in $(seq 1 20); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$WEB_PORT$PANEL_PATH/healthz" >/dev/null 2>&1; then
        upstream_ok=1
        break
    fi
    sleep 1
done

if [ "$upstream_ok" -ne 1 ]; then
    die "پنل روی 127.0.0.1:$WEB_PORT$PANEL_PATH بالا نیامد.
    لاگ را ببینید:  journalctl -u $SERVICE -n 50 --no-pager"
fi
ok "پنل روی 127.0.0.1:$WEB_PORT$PANEL_PATH جواب می‌دهد"

# ── nginx ────────────────────────────────────────────────────────────

step "پیکربندی nginx"

# سقف نرخ در http تعریف می‌شود نه در server، پس فایل جداست.
#
# چرا اصلاً سقف. لینک ورود یک توکن یک‌بارمصرف با عمر پنج دقیقه است؛
# حدس زدنش عملاً ناممکن است، ولی سقف نرخ هزینه‌ی حدس زدن را از
# «رایگان» به «غیرممکن» می‌برد و لاگ را هم از هزاران خط بیهوده نجات
# می‌دهد.
cat > /etc/nginx/conf.d/telkap-limits.conf <<'LIMITS'
limit_req_zone $binary_remote_addr zone=telkap_panel:10m rate=10r/s;
LIMITS

# صفحه‌ی فروش و مینی‌اپ از خودِ مخزن سرو می‌شوند، پس «git pull» هم
# به‌روزشان می‌کند. nginx با کاربر www-data می‌خواند و باید بتواند.
mkdir -p "$SITE_DIR"
chmod o+rx "$APP_DIR" "$SITE_DIR" 2>/dev/null || true

# سرصفحه‌های امنیتی در یک فایل جدا و از هر location اینclude می‌شوند.
#
# <b>چرا نه یک‌بار در سطح server.</b> در nginx، هر بلوکی که خودش
# add_header داشته باشد، همه‌ی add_headerهای بالادست را کنار می‌گذارد —
# ارث نمی‌رسد، جایگزین می‌شود. یعنی یک add_header در یک location،
# بی‌صدا HSTS و بقیه را از همان‌جا برمی‌دارد. با include، هر جا کامل
# است و HSTS هم که بعداً به این فایل اضافه می‌شود، همه‌جا می‌آید.
mkdir -p /etc/nginx/snippets
cat > /etc/nginx/snippets/telkap-headers.conf <<'HEADERS'
add_header X-Content-Type-Options "nosniff"     always;
add_header Referrer-Policy        "same-origin" always;
HEADERS

# نام‌های دیگر روی همان گواهی می‌نشینند ولی به نامِ اصلی تغییرمسیر
# می‌خورند. یک نشانیِ قانونی داشتن مهم است: کوکی نشست به دامنه بسته
# است، و اگر کسی از دو نام وارد شود دو نشست جدا می‌گیرد و بی‌دلیل
# دوباره ازش رمز خواسته می‌شود.
ALIAS_LIST="${ALIASES[*]}"

cat > "/etc/nginx/sites-available/telkap" <<NGINX
# فورواردبات — ساخته‌ی deploy/web-setup.sh
#
# دست‌نویس تغییرش ندهید مگر بدانید چه می‌کنید؛ اجرای دوباره‌ی اسکریپت
# این فایل را بازمی‌نویسد. بخش‌های مربوط به SSL را certbot خودش اضافه
# می‌کند.
#
#   /        صفحه‌ی فروش (فایل ثابت)
#   $PANEL_PATH   پنل مدیریت (پراکسی به ربات)
#   /app     مینی‌اپ تلگرام (فایل ثابت)

server {
    listen 80;
    listen [::]:80;
    server_name $DOMAIN;

    root $SITE_DIR;
    index index.html;

    # تصویر رسید تا چند مگابایت می‌شود
    client_max_body_size 10m;
    server_tokens off;

    # ── صفحه‌ی فروش ──────────────────────────────────────────────
    location / {
        try_files \$uri \$uri/ /index.html;
        include /etc/nginx/snippets/telkap-headers.conf;
        add_header X-Frame-Options "DENY" always;
    }

    # ── مینی‌اپ ──────────────────────────────────────────────────
    # مینی‌اپ داخل تلگرام و در یک iframe باز می‌شود، پس اینجا — و فقط
    # اینجا — قاب شدن ممنوع نیست؛ در عوض صریح گفته می‌شود که فقط
    # تلگرام اجازه دارد قابش کند.
    # رابط JSON مینی‌اپ، پیش از فایل‌های ثابتش.
    #
    # <b>این یکی جا افتاده بود و باگش بی‌صدا بود.</b> در nginx
    # طولانی‌ترین پیشوند برنده است، و بدون این بلوک «/app/api/me» هم
    # زیر «location /app» می‌افتاد. آنجا try_files به index.html
    # می‌رسید، یعنی رابط JSON با کد ۲۰۰ یک صفحه‌ی HTML برمی‌گرداند.
    # اپ هم آن را «کاربر ناشناس» می‌خواند و می‌گفت «هنوز ربات را
    # استارت نکرده‌اید» — پیامی که هیچ ربطی به علت نداشت.
    location /app/api {
        proxy_pass http://127.0.0.1:$WEB_PORT;

        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_http_version 1.1;

        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;

        limit_req zone=telkap_panel burst=20 nodelay;
        include /etc/nginx/snippets/telkap-headers.conf;
    }

    location /app {
        try_files \$uri \$uri/ /app/index.html =404;
        include /etc/nginx/snippets/telkap-headers.conf;
        add_header Content-Security-Policy "frame-ancestors https://web.telegram.org https://*.telegram.org" always;
    }

    # ── پنل مدیریت ───────────────────────────────────────────────
    location $PANEL_PATH {
        proxy_pass http://127.0.0.1:$WEB_PORT;

        proxy_set_header Host              \$host;
        proxy_set_header X-Real-IP         \$remote_addr;
        proxy_set_header X-Forwarded-For   \$proxy_add_x_forwarded_for;

        # پنل با همین می‌فهمد کاربر واقعاً از https آمده یا نه. بدون
        # آن، کوکی Secure فرستاده می‌شود ولی هشدارِ راهنما در لاگ
        # نمی‌آید و پیدا کردن مشکلِ «ورود در حلقه» ساعت‌ها طول می‌کشد.
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_http_version 1.1;

        # رسیدها از تلگرام گرفته می‌شوند و آن رفت‌وبرگشت گاهی کند است.
        # پیش‌فرض ۶۰ ثانیه‌ای nginx کافی است ولی صریح نوشته شده تا با
        # عوض شدن پیش‌فرض‌ها بی‌صدا نشکند.
        proxy_connect_timeout 10s;
        proxy_read_timeout    60s;
        proxy_send_timeout    60s;

        limit_req zone=telkap_panel burst=20 nodelay;

        include /etc/nginx/snippets/telkap-headers.conf;
        # پنل هیچ‌وقت نباید داخل قاب سایت دیگری باز شود — دکمه‌های
        # «تأیید» و «رد» پول واقعی جابه‌جا می‌کنند.
        add_header X-Frame-Options "DENY" always;
    }
}
NGINX

if [ -n "$ALIAS_LIST" ]; then
    cat >> "/etc/nginx/sites-available/telkap" <<NGINX

# نام‌های دیگر، فقط تغییرمسیر به نامِ اصلی
server {
    listen 80;
    listen [::]:80;
    server_name $ALIAS_LIST;
    return 301 https://$DOMAIN\$request_uri;
}
NGINX
fi

ln -sfn /etc/nginx/sites-available/telkap /etc/nginx/sites-enabled/telkap

# سایت پیش‌فرض روی همین پورت default_server است و اگر بماند، دامنه‌های
# ناشناخته صفحه‌ی «Welcome to nginx» می‌گیرند
rm -f /etc/nginx/sites-enabled/default

nginx -t >/dev/null 2>&1 || { nginx -t; die "پیکربندی nginx ایراد دارد."; }
systemctl enable --now nginx >/dev/null 2>&1 || true
systemctl reload nginx
ok "nginx روی پورت ۸۰ برای $DOMAIN"

# ── فایروال ──────────────────────────────────────────────────────────

if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "^Status: active"; then
    step "فایروال"
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
    ok "پورت‌های ۸۰ و ۴۴۳ باز شدند"
fi

# ── گواهی ────────────────────────────────────────────────────────────

step "گرفتن گواهی SSL"

# Let's Encrypt برای صدور گواهی باید از بیرون به پورت ۸۰ همین دامنه
# برسد. اگر فایروالِ مرکزِ داده جلویش را گرفته باشد، اینجا شکست
# می‌خورد و پیامش همان را می‌گوید.
certbot_args=(
    --nginx
    -d "$DOMAIN"
    --agree-tos
    --non-interactive
    --redirect              # http را به https می‌برد
    # اجرای دوباره‌ی این اسکریپت فایل nginx را از نو می‌نویسد، یعنی
    # خط‌های SSL که certbot اضافه کرده بود پاک می‌شوند. بدون این پرچم،
    # certbot می‌بیند گواهی هنوز معتبر است و در حالت غیرتعاملی به‌جای
    # نصب دوباره‌ی همان گواهی، خطا می‌دهد — و سایت روی http می‌ماند.
    --keep-until-expiring
    # <b>و این یکی، برای وقتی که نام تازه‌ای اضافه می‌شود.</b>
    # اگر از قبل گواهی‌ای برای زیرمجموعه‌ی این نام‌ها باشد — مثلاً
    # گواهیِ botpanel وقتی حالا forwardbot را هم می‌خواهیم — certbot
    # در حالت غیرتعاملی می‌ایستد و می‌پرسد «گسترش بدهم؟»، ولی جوابی
    # نمی‌گیرد و شکست می‌خورد. خطایش هم به گواهیِ قدیمی اشاره می‌کند
    # نه به پرچمِ نداشته، پس علتش دیر پیدا می‌شود.
    --expand
)

for name in "${ALIASES[@]}"; do
    certbot_args+=(-d "$name")
done

if [ -n "$EMAIL" ]; then
    certbot_args+=(-m "$EMAIL")
else
    warn "ایمیل ندادید؛ هشدار انقضای گواهی به جایی فرستاده نمی‌شود."
    certbot_args+=(--register-unsafely-without-email)
fi

if certbot "${certbot_args[@]}"; then
    ok "گواهی صادر و nginx تنظیم شد"
else
    die "گرفتن گواهی شکست خورد.
    رایج‌ترین علت‌ها:
      • پورت ۸۰ از بیرون بسته است (فایروال مرکز داده)
      • رکورد A هنوز پخش نشده — چند دقیقه بعد دوباره امتحان کنید
      • همین دامنه امروز چند بار امتحان شده و به سقف Let's Encrypt خورده

    nginx روی http کار می‌کند؛ فقط برای گرفتن گواهی دوباره اجرا کنید:
      sudo certbot --nginx -d $DOMAIN --redirect"
fi

# HSTS تازه حالا اضافه می‌شود که گواهی واقعاً هست. مرورگر این سرصفحه
# را روی http نادیده می‌گیرد، پس زودتر گذاشتنش خطرناک نبود — فقط
# بی‌اثر بود. اینجا بودنش یعنی هر خطی که در فایل هست، کاری هم می‌کند.
#
# ۱۸۰ روز، بدون preload. مدت‌های بلندتر و preload برگشت‌ناپذیرند: تا
# وقتی نگذشته، مرورگر حاضر نیست این دامنه را روی http باز کند حتی اگر
# خودتان بخواهید.
if ! grep -q "Strict-Transport-Security" /etc/nginx/snippets/telkap-headers.conf; then
    # داخل همان فایلِ مشترک، تا هر سه مسیر بگیرندش. اگر در فایل سایت
    # می‌رفت، locationهایی که add_header خودشان را دارند کنارش
    # می‌گذاشتند و پنل — که مهم‌ترینشان است — بی‌HSTS می‌ماند.
    printf '%s\n' \
        'add_header Strict-Transport-Security "max-age=15552000" always;' \
        >> /etc/nginx/snippets/telkap-headers.conf
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx && ok "HSTS فعال شد"
    else
        warn "افزودن HSTS پیکربندی را خراب کرد؛ برگردانده شد."
        sed -i '/Strict-Transport-Security/d' /etc/nginx/snippets/telkap-headers.conf
        nginx -t >/dev/null 2>&1 && systemctl reload nginx
    fi
fi

# ── تمدید خودکار ─────────────────────────────────────────────────────

step "تمدید خودکار"

# گواهی Let's Encrypt نود روزه است. اگر تمدید خودکار کار نکند، پنل
# دقیقاً سه ماه دیگر و بی‌هیچ هشداری از کار می‌افتد.
if systemctl is-enabled certbot.timer >/dev/null 2>&1; then
    ok "تایمر certbot فعال است"
    systemctl start certbot.timer >/dev/null 2>&1 || true
else
    systemctl enable --now certbot.timer >/dev/null 2>&1 \
        && ok "تایمر certbot روشن شد" \
        || warn "تایمر certbot پیدا نشد؛ تمدید را دستی بررسی کنید."
fi

if certbot renew --dry-run >/dev/null 2>&1; then
    ok "تست تمدید موفق بود"
else
    warn "تست تمدید شکست خورد. قبل از نود روز دیگر بررسی‌اش کنید:
    sudo certbot renew --dry-run"
fi

# ── بررسی نهایی ──────────────────────────────────────────────────────

step "بررسی نهایی"

if curl -fsS --max-time 10 "https://$DOMAIN$PANEL_PATH/healthz" 2>/dev/null | grep -q "^ok$"; then
    ok "https://$DOMAIN$PANEL_PATH/healthz جواب داد"
else
    warn "از خودِ سرور به https://$DOMAIN$PANEL_PATH نرسیدیم.
    اگر از مرورگر باز می‌شود مشکلی نیست — بعضی شبکه‌ها اجازه‌ی
    برگشتن به IP خودشان را نمی‌دهند."
fi

if [ -f "$SITE_DIR/index.html" ]; then
    ok "صفحه‌ی فروش: https://$DOMAIN/"
else
    warn "$SITE_DIR/index.html نیست؛ «/» چیزی برای نشان دادن ندارد."
fi

if [ -f "$SITE_DIR/app/index.html" ]; then
    ok "مینی‌اپ: https://$DOMAIN/app"
else
    warn "$SITE_DIR/app/index.html هنوز ساخته نشده؛ «/app» تا آن‌وقت ۴۰۴ می‌دهد."
fi

printf '\n%s✓ آماده است%s\n\n' "$G" "$N"
say "  صفحه‌ی فروش:  https://$DOMAIN/"
say "  پنل مدیریت:   https://$DOMAIN$PANEL_PATH"
say "  مینی‌اپ:       https://$DOMAIN/app"
if [ -n "${ALIASES[*]}" ]; then
    say ""
    say "  این نام‌ها هم به بالا تغییرمسیر می‌خورند: ${ALIASES[*]}"
fi
say ""
say "  برای ورود به پنل، در ربات: ⚙️ سیستم ← 🖥 پنل وب"
say "  یا مستقیم: https://$DOMAIN$PANEL_PATH/login"
say ""
if [ -n "$EMAIL" ]; then
    say "  گواهی هر ۹۰ روز خودکار تمدید می‌شود؛ هشدارش به $EMAIL می‌رسد."
else
    say "  گواهی هر ۹۰ روز خودکار تمدید می‌شود."
fi
