#!/bin/bash
# Bootstrap a fresh DigitalOcean droplet for LearnHouse.
# Run once as root: curl -fsSL https://raw.githubusercontent.com/Life2LaunchLabs/learnhouse-infra/main/setup.sh | bash
set -e

DEPLOY_DIR=/opt/learnhouse

echo "==> Installing Docker..."
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

echo "==> Installing Caddy..."
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

echo "==> Cloning infra repo..."
git clone https://github.com/Life2LaunchLabs/learnhouse-infra "$DEPLOY_DIR"

echo "==> Copying Caddyfile..."
cp "$DEPLOY_DIR/Caddyfile" /etc/caddy/Caddyfile
systemctl reload caddy

echo ""
echo "Done. Next steps:"
echo "  1. Copy your .env to $DEPLOY_DIR/.env  (use .env.example as a template)"
echo "  2. Authenticate with GHCR:"
echo "       echo YOUR_GITHUB_PAT | docker login ghcr.io -u YOUR_GITHUB_USER --password-stdin"
echo "  3. Start the app:"
echo "       cd $DEPLOY_DIR && docker compose up -d"
