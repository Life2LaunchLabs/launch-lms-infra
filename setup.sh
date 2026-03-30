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

gen_secret() {
  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
}

# ── Gather inputs ─────────────────────────────────────────────────────────────

echo ""
echo "LearnHouse Droplet Setup"
echo "========================"
echo ""

DROPLET_IP=$(curl -sf http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address || echo "")
SSLIP_DOMAIN="${DROPLET_IP}.sslip.io"
echo "--- Domain ---"
read -rp "Domain (or enter for ${SSLIP_DOMAIN}): " DOMAIN </dev/tty
DOMAIN="${DOMAIN:-$SSLIP_DOMAIN}"

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
NEXTAUTH_SECRET=$(gen_secret)

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
LEARNHOUSE_SITE_NAME=LearnHouse
LEARNHOUSE_SITE_DESCRIPTION=
LEARNHOUSE_CONTACT_EMAIL=

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

# ── Frontend ──────────────────────────────────────────
NEXT_PUBLIC_LEARNHOUSE_DOMAIN=${DOMAIN}
NEXT_PUBLIC_LEARNHOUSE_API_URL=https://${DOMAIN}/api/v1/
NEXT_PUBLIC_LEARNHOUSE_BACKEND_URL=https://${DOMAIN}/
NEXT_PUBLIC_LEARNHOUSE_HTTPS=true
NEXT_PUBLIC_LEARNHOUSE_MULTI_ORG=false
NEXT_PUBLIC_LEARNHOUSE_DEFAULT_ORG=
NEXTAUTH_URL=https://${DOMAIN}
NEXTAUTH_SECRET=${NEXTAUTH_SECRET}

# ── Security ──────────────────────────────────────────
LEARNHOUSE_AUTH_JWT_SECRET_KEY=${JWT_SECRET}
COLLAB_INTERNAL_KEY=${COLLAB_KEY}
LEARNHOUSE_INITIAL_ADMIN_EMAIL=admin@${DOMAIN}
LEARNHOUSE_INITIAL_ADMIN_PASSWORD=changeme

# ── Database ──────────────────────────────────────────
LEARNHOUSE_SQL_CONNECTION_STRING=postgresql+asyncpg://learnhouse:${POSTGRES_PASSWORD}@db:5432/learnhouse
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# ── Redis ─────────────────────────────────────────────
LEARNHOUSE_REDIS_CONNECTION_STRING=redis://redis:6379/learnhouse

# ── Email (configure when ready) ──────────────────────
LEARNHOUSE_EMAIL_PROVIDER=resend
LEARNHOUSE_SYSTEM_EMAIL_ADDRESS=
LEARNHOUSE_RESEND_API_KEY=

# ── Content delivery (filesystem until ready for S3) ──
LEARNHOUSE_CONTENT_DELIVERY_TYPE=filesystem
LEARNHOUSE_S3_API_BUCKET_NAME=
LEARNHOUSE_S3_API_ENDPOINT_URL=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

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
echo " LearnHouse is starting at https://${DOMAIN}"
echo "================================================================"
echo ""
echo " Initial admin password: changeme"
echo " Change it after first login."
echo ""
echo " Generated secrets (also saved in ${DEPLOY_DIR}/.env):"
echo "   Postgres password : ${POSTGRES_PASSWORD}"
echo "   JWT secret        : ${JWT_SECRET}"
echo "   Collab key        : ${COLLAB_KEY}"
echo ""
echo " When ready, configure email, S3, and other options in:"
echo "   ${DEPLOY_DIR}/.env  (then: docker compose up -d)"
echo ""
echo " Remember to delete your temporary GitHub PAT:"
echo "   https://github.com/settings/tokens"
echo "================================================================"
