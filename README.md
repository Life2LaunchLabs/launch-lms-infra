# learnhouse-infra

Deployment infrastructure for [LearnHouse](https://github.com/learnhouse/app) on a DigitalOcean Droplet.

## How it works

- `setup.sh` — run once on a fresh droplet to install Docker, Caddy, and configure everything
- `deploy.sh` — called by GitHub Actions on every push to `prod` to pull the latest image and restart
- `docker-compose.yml` — runs the LearnHouse container from GHCR
- `Caddyfile` — Caddy reverse proxy with automatic TLS
- `.env.example` — all supported config variables with descriptions

## Bootstrap a new droplet

```bash
curl -fsSL https://raw.githubusercontent.com/Life2LaunchLabs/learnhouse-infra/main/setup.sh | bash
```

The script will prompt for:
- **Domain** — the domain pointing at this droplet
- **GitHub username** — for pulling the image from GHCR
- **GitHub PAT** — a Personal Access Token with `read:packages` scope (create at github.com/settings/tokens)

After setup, fill in the remaining required values in `/opt/learnhouse/.env`:

| Variable | How to generate |
|---|---|
| `LEARNHOUSE_AUTH_JWT_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `COLLAB_INTERNAL_KEY` | same |
| `LEARNHOUSE_INITIAL_ADMIN_PASSWORD` | choose a strong password |
| `LEARNHOUSE_SQL_CONNECTION_STRING` | from DO Managed PostgreSQL console — use `postgresql+asyncpg://` scheme |
| `LEARNHOUSE_REDIS_CONNECTION_STRING` | from DO Managed Redis console — use `rediss://` if TLS enabled |

Then start the app:

```bash
cd /opt/learnhouse && docker compose up -d
```

## Automatic deploys

Every push to the `prod` branch of the main repo triggers a GitHub Actions workflow that:
1. Builds and pushes a new image to GHCR
2. SSHes into the droplet and runs `deploy.sh`

Required GitHub secrets on the main repo:

| Secret | Value |
|---|---|
| `DROPLET_HOST` | Droplet IP address |
| `DROPLET_USER` | `root` (or deploy user) |
| `DROPLET_SSH_KEY` | Private SSH key for the droplet |
| `PROD_ENV_FILE` | Full contents of your `.env` file (optional — keeps secrets in sync) |

## Updating config

To change `docker-compose.yml`, `Caddyfile`, or `deploy.sh`: commit to this repo. The next deploy will `git pull` and pick up the changes automatically.
