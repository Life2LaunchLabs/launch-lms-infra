# launch-lms-infra

Deployment infrastructure for Launch LMS on a DigitalOcean Droplet.

## Architecture

- **Caddy** — TLS termination, reverse proxy (ports 80/443 on host)
- **Launch LMS container** — Next.js + FastAPI + Hocuspocus + internal Nginx, bound to `127.0.0.1:8080`
- **PostgreSQL** — pgvector:pg16, data persisted in Docker volume
- **Redis** — redis:7-alpine with AOF persistence

All containers run via Docker Compose. Caddy auto-provisions TLS via Let's Encrypt.

## Files

| File | Purpose |
|---|---|
| `setup.sh` | Bootstrap a fresh droplet — installs Docker, Caddy, pulls image, starts everything |
| `deploy.sh` | Called by GitHub Actions on every push to `prod` |
| `docker-compose.yml` | All services |
| `Caddyfile` | Reverse proxy config — edit domain here after setup |
| `.env.example` | All supported config variables |

## Bootstrap a new droplet

**Prerequisites:**
- Ubuntu 24.04 droplet
- DNS A record pointing your domain at the droplet IP
- GitHub PAT (7-day expiry) with `read:packages` scope — [create one here](https://github.com/settings/tokens/new)

**Run:**
```bash
curl -fsSL https://raw.githubusercontent.com/Life2LaunchLabs/launch-lms-infra/main/setup.sh | bash
```

The script prompts for your domain and GitHub credentials, generates all secrets, and starts the app. Initial admin login is printed at the end.

**After setup:**
- Change the admin password at `https://yourdomain.com/login`
- Delete your temporary GitHub PAT at github.com/settings/tokens
- Configure email, S3, AI, etc. in `/opt/launch-lms/.env`, then `docker compose -f /opt/launch-lms/docker-compose.yml up -d`

## Automatic deploys

Every push to the `prod` branch of the main repo builds a new image and deploys to the droplet automatically.

Required secrets in the main GitHub repo (`Settings → Secrets → Actions`):

| Secret | Value |
|---|---|
| `DROPLET_HOST` | Droplet IP address |
| `DROPLET_USER` | SSH user on the droplet, usually `root` |
| `DROPLET_SSH_KEY` | Private key contents for a deploy-only SSH key |

SSH setup:

1. Generate a new key pair on your local machine:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/launchlms_deploy
   ```
2. Add the public key to the droplet:
    ```bash
    ssh-copy-id -i ~/.ssh/launchlms_deploy.pub root@your_droplet_ip
    ```
    Or append `~/.ssh/launchlms_deploy.pub` to `~/.ssh/authorized_keys` for your deploy user.
3. Add the private key to GitHub as DROPLET_SSH_KEY:
    ```bash
    cat ~/.ssh/launchlms_deploy
    ```
The deploy job uses GITHUB_TOKEN to pull from GHCR, so no registry credentials are stored on the droplet.

## Key env vars

| Variable | Notes |
|---|---|
| `NEXT_PUBLIC_LAUNCHLMS_API_URL` | Public URL for browser-side API calls |
| `LAUNCHLMS_INTERNAL_API_URL` | Internal URL for server-side API calls — must be `http://localhost/api/v1/` to avoid TLS loop through Caddy |
| `LAUNCHLMS_SQL_CONNECTION_STRING` | Use `postgresql+psycopg2://` scheme (app uses sync SQLAlchemy) |
| `LAUNCHLMS_REDIS_CONNECTION_STRING` | Use `redis://redis:6379/launchlms` for local, `rediss://` for managed Redis |
| `COLLAB_INTERNAL_KEY` | Shared secret between the API and the Hocuspocus collab server |

## Manual operations

```bash
# View logs
docker compose -f /opt/launch-lms/docker-compose.yml logs -f launch-lms

# Restart
docker compose -f /opt/launch-lms/docker-compose.yml up -d

# Update domain
sed -i 's/old.domain.com/new.domain.com/' /etc/caddy/Caddyfile
sed -i 's/old.domain.com/new.domain.com/g' /opt/launch-lms/.env
systemctl reload caddy
docker compose -f /opt/launch-lms/docker-compose.yml up -d
```
