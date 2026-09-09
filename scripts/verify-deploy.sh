#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/load-release-env.sh
container=$(docker compose ps -q launch-lms)
test -n "$container"
test "$(docker inspect --format '{{.Config.Image}}' "$container")" = "$LAUNCHLMS_IMAGE"
# Verify local image identity as well as the reference supplied to Docker.
test "$(docker inspect --format '{{.Image}}' "$container")" = "$(docker image inspect --format '{{.Id}}' "$LAUNCHLMS_IMAGE")"
metadata=$(docker compose exec -T launch-lms cat /app/build-info.json)
export EXPECTED_COMMIT="$LAUNCHLMS_RELEASE_COMMIT_SHA"
head=$(printf '%s' "$metadata" | python3 -c 'import json,os,sys; d=json.load(sys.stdin); assert d["commit_sha"] == os.environ["EXPECTED_COMMIT"]; assert d["alembic_head"]; print(d["alembic_head"])')
revision=$(docker compose run --rm --no-deps --entrypoint sh migrate -lc 'cd /app/api && uv run alembic current' | tail -n 1 | awk '{print $1}')
test "$head" = "$revision"
for service in db redis embeddings; do
  id=$(docker compose ps -q "$service")
  test "$(docker inspect --format '{{.State.Health.Status}}' "$id")" = healthy
done
# Retry boot readiness; fail before recording deployment success.
ready=false
for _attempt in $(seq 1 90); do
  if docker compose exec -T launch-lms sh -c 'curl -fsS --max-time 5 http://localhost/api/v1/health && curl -fsS --max-time 5 http://localhost:8000/login >/dev/null && curl -fsS --max-time 5 http://localhost:4000/health'; then
    ready=true
    break
  fi
  sleep 2
done
[[ "$ready" == true ]] || { echo 'Application readiness failed'; exit 1; }
docker compose exec -T launch-lms python -c '
import json, urllib.request
r=urllib.request.Request("http://embeddings:11434/api/embed", data=json.dumps({"model":"all-minilm:33m","input":"deployment check"}).encode(), headers={"Content-Type":"application/json"})
with urllib.request.urlopen(r, timeout=60) as response:
    assert len(json.load(response)["embeddings"][0]) == 384
'
docker compose exec -T launch-lms sh -lc 'cd /app/api && uv run python -' < scripts/verify-storage.py

echo "Verified image, commit, schema, dependencies, API, frontend, collaboration, and embeddings."
