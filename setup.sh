#!/bin/bash
# Bootstrap a fresh DigitalOcean droplet for Launch LMS.
# Run once as root: curl -fsSL https://raw.githubusercontent.com/Life2LaunchLabs/launch-lms-infra/main/setup.sh | bash
set -e

DEPLOY_DIR=/opt/launch-lms

# ── Guards ────────────────────────────────────────────────────────────────────

if [[ -f "$DEPLOY_DIR/.env" ]]; then
  echo "Existing deployment detected at $DEPLOY_DIR."
  echo "To apply infra changes, run: $DEPLOY_DIR/deploy.sh"
  exit 1
fi

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
echo "Launch LMS Droplet Setup"
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
echo "--- DigitalOcean DNS (for wildcard TLS) ---"
echo "Create an API token with Domain read/write scope at:"
echo "  https://cloud.digitalocean.com/account/api/tokens"
prompt DO_AUTH_TOKEN "DigitalOcean API token" true

# ── Auto-generate secrets ─────────────────────────────────────────────────────

echo ""
echo "==> Generating secrets..."
POSTGRES_PASSWORD=$(gen_secret)
JWT_SECRET=$(gen_secret)
COLLAB_KEY=$(gen_secret)

# ── Install Docker ────────────────────────────────────────────────────────────

echo "==> Installing Docker..."
apt-get update -q
apt-get install -y -q git curl
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
systemctl enable docker
systemctl start docker

# ── Clone infra repo ──────────────────────────────────────────────────────────

echo "==> Cloning infra repo..."
git clone https://github.com/Life2LaunchLabs/launch-lms-infra "$DEPLOY_DIR"

# ── Write .env ────────────────────────────────────────────────────────────────

echo "==> Writing .env..."
cat > "$DEPLOY_DIR/.env" <<EOF
# ── Site ──────────────────────────────────────────────
LAUNCHLMS_SITE_NAME=Launch LMS
LAUNCHLMS_SITE_DESCRIPTION=
LAUNCHLMS_CONTACT_EMAIL=

# ── Hosting ───────────────────────────────────────────
LAUNCHLMS_DOMAIN=${DOMAIN}
LAUNCHLMS_FRONTEND_DOMAIN=${DOMAIN}
LAUNCHLMS_SSL=false
LAUNCHLMS_PORT=9000
LAUNCHLMS_USE_DEFAULT_ORG=true
LAUNCHLMS_SELF_HOSTED=true
LAUNCHLMS_ALLOWED_ORIGINS=https://${DOMAIN}
LAUNCHLMS_ALLOWED_REGEXP=https://${DOMAIN//./\\.}
LAUNCHLMS_COOKIE_DOMAIN=${DOMAIN}
LAUNCHLMS_ENV=prod

# ── Frontend ──────────────────────────────────────────
NEXT_PUBLIC_LAUNCHLMS_DOMAIN=${DOMAIN}
NEXT_PUBLIC_LAUNCHLMS_API_URL=
NEXT_PUBLIC_LAUNCHLMS_BACKEND_URL=https://${DOMAIN}/
LAUNCHLMS_INTERNAL_API_URL=http://localhost/api/v1/
NEXT_PUBLIC_LAUNCHLMS_HTTPS=true
NEXT_PUBLIC_LAUNCHLMS_DEFAULT_ORG=
NEXT_PUBLIC_COLLAB_URL=wss://${DOMAIN}/collab

# ── Security ──────────────────────────────────────────
LAUNCHLMS_AUTH_JWT_SECRET_KEY=${JWT_SECRET}
COLLAB_INTERNAL_KEY=${COLLAB_KEY}
LAUNCHLMS_INITIAL_ADMIN_EMAIL=admin@${DOMAIN}
LAUNCHLMS_INITIAL_ADMIN_PASSWORD=changeme

# ── Database ──────────────────────────────────────────
LAUNCHLMS_SQL_CONNECTION_STRING=postgresql+psycopg2://launchlms:${POSTGRES_PASSWORD}@db:5432/launchlms
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}

# ── Redis ─────────────────────────────────────────────
LAUNCHLMS_REDIS_CONNECTION_STRING=redis://redis:6379/launchlms

# ── DigitalOcean DNS (Caddy wildcard TLS) ─────────────
DO_AUTH_TOKEN=${DO_AUTH_TOKEN}

# ── Email (configure when ready) ──────────────────────
LAUNCHLMS_EMAIL_PROVIDER=resend
LAUNCHLMS_SYSTEM_EMAIL_ADDRESS=
LAUNCHLMS_RESEND_API_KEY=

# ── Content delivery (filesystem until ready for S3) ──
LAUNCHLMS_CONTENT_DELIVERY_TYPE=filesystem
LAUNCHLMS_S3_API_BUCKET_NAME=
LAUNCHLMS_S3_API_ENDPOINT_URL=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=

# ── AI (optional — add key to enable) ─────────────────
LAUNCHLMS_IS_AI_ENABLED=false
LAUNCHLMS_GEMINI_API_KEY=

# ── Payments (optional) ───────────────────────────────
LAUNCHLMS_STRIPE_SECRET_KEY=
LAUNCHLMS_STRIPE_PUBLISHABLE_KEY=
LAUNCHLMS_STRIPE_WEBHOOK_STANDARD_SECRET=
LAUNCHLMS_STRIPE_WEBHOOK_CONNECT_SECRET=
EOF

chmod 600 "$DEPLOY_DIR/.env"

# ── Generate processed Caddyfile ──────────────────────────────────────────────

sed "s/your.domain.com/$DOMAIN/g" "$DEPLOY_DIR/Caddyfile" > "$DEPLOY_DIR/Caddyfile.active"

# ── Pull image and start ──────────────────────────────────────────────────────

source "$DEPLOY_DIR/scripts/load-release-env.sh"

echo ""
echo "Create a short-lived PAT (7 days) with read:packages scope at:"
echo "  https://github.com/settings/tokens/new"
echo "Delete it once setup is complete."
echo ""
prompt GHCR_PAT "GitHub PAT (temporary)" true
echo "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USER" --password-stdin
docker pull "${LAUNCHLMS_IMAGE}"
docker logout ghcr.io

echo "==> Starting database and redis..."
docker compose -f "$DEPLOY_DIR/docker-compose.yml" up -d db redis

echo "==> Running database migrations..."
docker compose -f "$DEPLOY_DIR/docker-compose.yml" run --rm migrate

echo "==> Starting all services..."
docker compose -f "$DEPLOY_DIR/docker-compose.yml" rm -sf launch-lms || true
docker compose -f "$DEPLOY_DIR/docker-compose.yml" up -d launch-lms caddy
"$DEPLOY_DIR/scripts/verify-deploy.sh"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "================================================================"
echo " Launch LMS is starting at https://${DOMAIN}"
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
