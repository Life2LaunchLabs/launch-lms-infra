#!/bin/bash
# Bootstrap a fresh DigitalOcean droplet for LearnHouse.
# Run once as root: curl -fsSL https://raw.githubusercontent.com/Life2LaunchLabs/learnhouse-infra/main/setup.sh | bash
set -e

DEPLOY_DIR=/opt/learnhouse

# ── Helpers ───────────────────────────────────────────────────────────────────

prompt() {
  local var=$1 msg=$2 secret=${3:-false}
  while true; do
    if [[ "$secret" == "true" ]]; then
      read -rsp "$msg: " val </dev/tty; echo ""
    else
      read -rp "$msg: " val </dev/tty
    fi
    [[ -n "$val" ]] && { printf -v "$var" '%s' "$val"; return; }
    echo "  This field is required."
  done
}

prompt_optional() {
  local var=$1 msg=$2 default=$3
  read -rp "$msg${default:+ [$default]}: " val </dev/tty
  printf -v "$var" '%s' "${val:-$default}"
}

prompt_choice() {
  local var=$1 msg=$2; shift 2
  local opts=("$@")
  while true; do
    echo "$msg"
    for i in "${!opts[@]}"; do echo "  $((i+1))) ${opts[$i]}"; done
    read -rp "Choice [1-${#opts[@]}]: " choice </dev/tty
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#opts[@]} )); then
      printf -v "$var" '%s' "${opts[$((choice-1))]}"; return
    fi
    echo "  Invalid choice."
  done
}

gen_secret() {
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
}

# ── Gather inputs ─────────────────────────────────────────────────────────────

echo ""
echo "LearnHouse Droplet Setup"
echo "========================"
echo ""

echo "--- Site ---"
prompt SITE_NAME       "Site name (e.g. My LMS)"
prompt_optional SITE_DESCRIPTION "Site description" ""
prompt CONTACT_EMAIL   "Contact email"

echo ""
echo "--- Domain ---"
prompt DOMAIN "Domain (or IP.sslip.io if no domain yet)"

echo ""
echo "--- Admin account ---"
prompt ADMIN_PASSWORD "Initial admin password" true

echo ""
echo "--- Email ---"
prompt_choice EMAIL_PROVIDER "Email provider?" "resend" "smtp"
if [[ "$EMAIL_PROVIDER" == "resend" ]]; then
  prompt RESEND_API_KEY "Resend API key" true
  prompt SYSTEM_EMAIL   "System sender email (e.g. noreply@yourdomain.com)"
else
  prompt SMTP_HOST     "SMTP host"
  prompt_optional SMTP_PORT "SMTP port" "587"
  prompt SMTP_USERNAME "SMTP username"
  prompt SMTP_PASSWORD "SMTP password" true
  prompt SYSTEM_EMAIL  "System sender email"
fi

echo ""
echo "--- Content delivery ---"
prompt_choice CONTENT_DELIVERY "File storage?" "filesystem (local, simpler)" "s3api (AWS S3 or compatible)"
if [[ "$CONTENT_DELIVERY" == s3api* ]]; then
  CONTENT_DELIVERY=s3api
  prompt S3_BUCKET       "S3 bucket name"
  prompt S3_ENDPOINT     "S3 endpoint URL (e.g. https://s3.amazonaws.com)"
  prompt AWS_KEY_ID      "AWS access key ID" true
  prompt AWS_KEY_SECRET  "AWS secret access key" true
else
  CONTENT_DELIVERY=filesystem
fi

echo ""
echo "--- GitHub (for image pull) ---"
prompt GHCR_USER "GitHub username"
echo ""
echo "Create a short-lived PAT (7 days) with read:packages scope at:"
echo "  https://github.com/settings/tokens/new"
echo "Delete it once setup is complete."
echo ""
prompt GHCR_PAT "GitHub PAT (temporary)" true

# ── Auto-generate secrets ─────────────────────────────────────────────────────

echo ""
echo "==> Generating secrets..."
POSTGRES_PASSWORD=$(gen_secret)
JWT_SECRET=$(gen_secret)
COLLAB_KEY=$(gen_secret)

# ── Install Docker ─────────────────────────────────────────────────────────────

echo "==> Installing Docker..."
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

# ── Install Caddy ──────────────────────────────────────────────────────────────

echo "==> Installing Caddy..."
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

# ── Clone infra repo ───────────────────────────────────────────────────────────

echo "==> Cloning infra repo..."
git clone https://github.com/Life2LaunchLabs/learnhouse-infra "$DEPLOY_DIR"

# ── Configure Caddy ───────────────────────────────────────────────────────────

echo "==> Configuring Caddy..."
sed "s/your.domain.com/$DOMAIN/" "$DEPLOY_DIR/Caddyfile" > /etc/caddy/Caddyfile
systemctl enable docker
systemctl start docker
systemctl reload caddy

# ── Write .env ────────────────────────────────────────────────────────────────

echo "==> Writing .env..."
cat > "$DEPLOY_DIR/.env" <<EOF
# ── Site ──────────────────────────────────────────────
LEARNHOUSE_SITE_NAME=${SITE_NAME}
LEARNHOUSE_SITE_DESCRIPTION=${SITE_DESCRIPTION}
LEARNHOUSE_CONTACT_EMAIL=${CONTACT_EMAIL}

# ── Hosting ───────────────────────────────────────────
LEARNHOUSE_DOMAIN=${DOMAIN}
LEARNHOUSE_FRONTEND_DOMAIN=${DOMAIN}
LEARNHOUSE_SSL=true
LEARNHOUSE_PORT=9000
LEARNHOUSE_USE_DEFAULT_ORG=true
LEARNHOUSE_SELF_HOSTED=true
LEARNHOUSE_ALLOWED_ORIGINS=https://${DOMAIN}
LEARNHOUSE_ALLOWED_REGEXP=https://${DOMAIN//./\\.}
LEARNHOUSE_COOKIE_DOMAIN=${DOMAIN}
LEARNHOUSE_ENV=prod

# ── Security ──────────────────────────────────────────
LEARNHOUSE_AUTH_JWT_SECRET_KEY=${JWT_SECRET}
COLLAB_INTERNAL_KEY=${COLLAB_KEY}
LEARNHOUSE_INITIAL_ADMIN_PASSWORD=${ADMIN_PASSWORD}

# ── Database ──────────────────────────────────────────
LEARNHOUSE_SQL_CONNECTION_STRING=postgresql+asyncpg://learnhouse:${POSTGRES_PASSWORD}@db:5432/learnhouse
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# ── Redis ─────────────────────────────────────────────
LEARNHOUSE_REDIS_CONNECTION_STRING=redis://redis:6379

# ── Email ─────────────────────────────────────────────
LEARNHOUSE_EMAIL_PROVIDER=${EMAIL_PROVIDER}
LEARNHOUSE_SYSTEM_EMAIL_ADDRESS=${SYSTEM_EMAIL}
LEARNHOUSE_RESEND_API_KEY=${RESEND_API_KEY:-}
LEARNHOUSE_SMTP_HOST=${SMTP_HOST:-}
LEARNHOUSE_SMTP_PORT=${SMTP_PORT:-587}
LEARNHOUSE_SMTP_USERNAME=${SMTP_USERNAME:-}
LEARNHOUSE_SMTP_PASSWORD=${SMTP_PASSWORD:-}

# ── Content delivery ──────────────────────────────────
LEARNHOUSE_CONTENT_DELIVERY_TYPE=${CONTENT_DELIVERY}
LEARNHOUSE_S3_API_BUCKET_NAME=${S3_BUCKET:-}
LEARNHOUSE_S3_API_ENDPOINT_URL=${S3_ENDPOINT:-}
AWS_ACCESS_KEY_ID=${AWS_KEY_ID:-}
AWS_SECRET_ACCESS_KEY=${AWS_KEY_SECRET:-}

# ── AI (optional — add key to enable) ─────────────────
LEARNHOUSE_IS_AI_ENABLED=false
LEARNHOUSE_GEMINI_API_KEY=

# ── Payments (optional) ───────────────────────────────
LEARNHOUSE_STRIPE_SECRET_KEY=
LEARNHOUSE_STRIPE_PUBLISHABLE_KEY=
LEARNHOUSE_STRIPE_WEBHOOK_STANDARD_SECRET=
LEARNHOUSE_STRIPE_WEBHOOK_CONNECT_SECRET=
EOF

chmod 600 "$DEPLOY_DIR/.env"

# ── Pull image and logout ─────────────────────────────────────────────────────

echo "==> Pulling LearnHouse image..."
echo "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker compose -f "$DEPLOY_DIR/docker-compose.yml" pull
docker logout ghcr.io

# ── Start ─────────────────────────────────────────────────────────────────────

echo "==> Starting services..."
docker compose -f "$DEPLOY_DIR/docker-compose.yml" up -d

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo " LearnHouse is starting up at https://${DOMAIN}"
echo "================================================================"
echo ""
echo " Generated secrets (save these somewhere safe):"
echo "   Postgres password : ${POSTGRES_PASSWORD}"
echo "   JWT secret        : ${JWT_SECRET}"
echo "   Collab key        : ${COLLAB_KEY}"
echo ""
echo " These are also saved in ${DEPLOY_DIR}/.env"
echo ""
echo " Remember to delete your temporary GitHub PAT:"
echo "   https://github.com/settings/tokens"
echo "================================================================"
