#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOCK_FILE="${DEPLOY_DIR}/release.lock.json"

if [[ ! -f "${LOCK_FILE}" ]]; then
  echo "release.lock.json not found at ${LOCK_FILE}" >&2
  exit 1
fi

eval "$(
python3 - "${LOCK_FILE}" <<'PY'
import json
import shlex
import sys
from pathlib import Path

lock = json.loads(Path(sys.argv[1]).read_text())
repository = lock["image_repository"]
digest = lock.get("image_digest")
image_ref = lock.get("image_ref")

resolved_image = f"{repository}@{digest}" if digest else image_ref

if not resolved_image:
    raise SystemExit("release.lock.json must contain image_ref or image_digest")

values = {
    "LAUNCHLMS_IMAGE": resolved_image,
    "LAUNCHLMS_IMAGE_REPOSITORY": repository,
    "LAUNCHLMS_IMAGE_DIGEST": digest or "",
    "LAUNCHLMS_IMAGE_TAG": lock.get("image_tag") or "",
    "LAUNCHLMS_RELEASE_VERSION": lock.get("version") or "unknown",
    "LAUNCHLMS_RELEASE_COMMIT_SHA": lock.get("commit_sha") or "unknown",
    "LAUNCHLMS_RELEASED_AT": lock.get("released_at") or "unknown",
}

for key, value in values.items():
    print(f'export {key}={shlex.quote(value)}')
PY
)"
