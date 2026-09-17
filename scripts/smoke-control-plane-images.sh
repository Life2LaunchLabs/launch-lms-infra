#!/usr/bin/env bash
set -euo pipefail

api_image="launch-operations-api:contract"
web_image="launch-operations-web:contract"

docker build -f apps/control-plane/api/Dockerfile -t "$api_image" .
docker run --rm --entrypoint python "$api_image" -c \
  "import main; paths={route.path for route in main.app.routes}; assert '/healthz' in paths; assert '/api/v1/embed/session' in paths; assert '/api/v1/embed/feedback' in paths"

docker build -f apps/control-plane/web/Dockerfile -t "$web_image" .
docker run --rm --entrypoint sh "$web_image" -ec \
  'test -s /usr/share/nginx/html/index.html; test -s /usr/share/nginx/html/sdk/v1/loader.js; test -s /usr/share/nginx/html/embed/v1/index.html; ! grep -R "localStorage\|sessionStorage" /usr/share/nginx/html/sdk/v1 /usr/share/nginx/html/embed/v1'
