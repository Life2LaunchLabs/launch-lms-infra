#!/usr/bin/env bash
# Optional integration rehearsal with a locally built app image. No cloud access.
set -euo pipefail
: "${IMAGE:?Set IMAGE to the local app image}"
cd "$(dirname "$0")/.."
prefix="bot99-copy-$$"
work=$(mktemp -d)
cleanup() {
  docker rm -f "$prefix-db" >/dev/null 2>&1 || true
  docker network rm "$prefix" >/dev/null 2>&1 || true
  rm -rf -- "$work"
}
trap cleanup EXIT
docker network create --internal "$prefix" >/dev/null
docker run -d --name "$prefix-db" --network "$prefix" --network-alias db \
  -e POSTGRES_USER=launchlms -e POSTGRES_PASSWORD=rehearsal -e POSTGRES_DB=launchlms pgvector/pgvector:pg16 >/dev/null
for _attempt in $(seq 1 60); do
  if docker exec "$prefix-db" pg_isready -U launchlms >/dev/null; then break; fi
  sleep 1
done
docker run --rm --network "$prefix" \
  -e LAUNCHLMS_AUTH_JWT_SECRET_KEY=rehearsal-only-synthetic-secret-key \
  -e COLLAB_INTERNAL_KEY=rehearsal-only \
  -e LAUNCHLMS_SQL_CONNECTION_STRING=postgresql+psycopg2://launchlms:rehearsal@db:5432/launchlms \
  --entrypoint sh "$IMAGE" -lc 'cd /app/api && ./scripts/run_alembic_migrations.sh'
docker exec "$prefix-db" psql -U launchlms -d launchlms -v ON_ERROR_STOP=1 -c "CREATE TABLE bot99_probe (id integer PRIMARY KEY, password text, email text, links jsonb); INSERT INTO bot99_probe VALUES (1, 'preserved-hash', 'test@prod.example.org', '{\"url\":\"https://school.prod.example.org/content/file.pdf\"}');"
docker exec "$prefix-db" pg_dump -U launchlms -d launchlms -Fc > "$work/database.dump"
docker exec "$prefix-db" createdb -U launchlms launchlms_refresh_20260908
docker exec -i "$prefix-db" pg_restore -U launchlms --no-owner --no-acl --exit-on-error -d launchlms_refresh_20260908 < "$work/database.dump"
docker run --rm -i --network "$prefix" \
  -e LAUNCHLMS_SQL_CONNECTION_STRING=postgresql+psycopg2://launchlms:rehearsal@db:5432/launchlms_refresh_20260908 \
  -e SOURCE_DOMAIN=prod.example.org -e TARGET_DOMAIN=test.example.net \
  --entrypoint sh "$IMAGE" -lc 'cd /app/api && uv run python -' < scripts/sanitize-copy.py
for database in launchlms launchlms_refresh_20260908; do
  test "$(docker exec "$prefix-db" psql -U launchlms -d "$database" -tAc 'SELECT password FROM bot99_probe')" = preserved-hash
done
test "$(docker exec "$prefix-db" psql -U launchlms -d launchlms -tAc "SELECT links->>'url' FROM bot99_probe")" = https://school.prod.example.org/content/file.pdf
test "$(docker exec "$prefix-db" psql -U launchlms -d launchlms_refresh_20260908 -tAc "SELECT links->>'url' FROM bot99_probe")" = https://school.test.example.net/content/file.pdf
# Verify the actual network policy blocks external sockets, rather than only
# asserting a Compose property. DNS isn't needed for this probe.
docker run --rm --network "$prefix" --entrypoint python "$IMAGE" -c '
import socket
try:
    socket.create_connection(("1.1.1.1",443),timeout=3)
except OSError:
    print("External network blocked as expected")
else:
    raise SystemExit("Unexpected external network access")
'
echo 'PostgreSQL restore/sanitize and network isolation rehearsal passed.'
