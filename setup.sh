#!/bin/bash
# Bootstrap a fresh DigitalOcean droplet for LearnHouse.
# Run once as root: curl -fsSL https://raw.githubusercontent.com/Life2LaunchLabs/learnhouse-infra/main/setup.sh | bash
set -e

DEPLOY_DIR=/opt/learnhouse

# ── Gather required inputs ─────────────────────────────────────────────────────

echo ""
echo "LearnHouse Droplet Setup"
echo "========================"
echo ""

read -rp "Domain (e.g. learn.yourdomain.com): " DOMAIN </dev/tty
while [[ -z "$DOMAIN" ]]; do
  echo "Domain is required."
  read -rp "Domain: " DOMAIN </dev/tty
done

read -rp "GitHub username (for GHCR image pull): " GHCR_USER </dev/tty
while [[ -z "$GHCR_USER" ]]; do
  echo "GitHub username is required."
  read -rp "GitHub username: " GHCR_USER </dev/tty
done

read -rsp "GitHub PAT with read:packages scope: " GHCR_PAT </dev/tty
echo ""
while [[ -z "$GHCR_PAT" ]]; do
  echo "GitHub PAT is required."
  read -rsp "GitHub PAT: " GHCR_PAT </dev/tty
  echo ""
done

# ── Install Docker ─────────────────────────────────────────────────────────────

echo ""
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
systemctl reload caddy

# ── Ensure Docker is running ──────────────────────────────────────────────────

echo "==> Starting Docker..."
systemctl enable docker
systemctl start docker

# ── Authenticate with GHCR ────────────────────────────────────────────────────

echo "==> Authenticating with GitHub Container Registry..."
echo "$GHCR_PAT" | docker login ghcr.io -u "$GHCR_USER" --password-stdin

# ── Create .env from example ──────────────────────────────────────────────────

echo "==> Creating .env from template..."
cp "$DEPLOY_DIR/.env.example" "$DEPLOY_DIR/.env"
sed -i "s/^LEARNHOUSE_DOMAIN=.*/LEARNHOUSE_DOMAIN=$DOMAIN/" "$DEPLOY_DIR/.env"
sed -i "s/^LEARNHOUSE_FRONTEND_DOMAIN=.*/LEARNHOUSE_FRONTEND_DOMAIN=$DOMAIN/" "$DEPLOY_DIR/.env"
sed -i "s/^LEARNHOUSE_COOKIE_DOMAIN=.*/LEARNHOUSE_COOKIE_DOMAIN=$DOMAIN/" "$DEPLOY_DIR/.env"
sed -i "s/^LEARNHOUSE_ALLOWED_ORIGINS=.*/LEARNHOUSE_ALLOWED_ORIGINS=https:\/\/$DOMAIN/" "$DEPLOY_DIR/.env"
sed -i "s/^LEARNHOUSE_ALLOWED_REGEXP=.*/LEARNHOUSE_ALLOWED_REGEXP=https:\/\/${DOMAIN//./\\.}/" "$DEPLOY_DIR/.env"

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo "Setup complete. Before starting the app, fill in the remaining values in $DEPLOY_DIR/.env:"
echo ""
echo "  Required:"
echo "    LEARNHOUSE_AUTH_JWT_SECRET_KEY  (generate: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\")"
echo "    COLLAB_INTERNAL_KEY             (generate same way)"
echo "    LEARNHOUSE_INITIAL_ADMIN_PASSWORD"
echo "    LEARNHOUSE_SQL_CONNECTION_STRING"
echo "    LEARNHOUSE_REDIS_CONNECTION_STRING"
echo ""
echo "  Then start the app:"
echo "    cd $DEPLOY_DIR && docker compose up -d"
