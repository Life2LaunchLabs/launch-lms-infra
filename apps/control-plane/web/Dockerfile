FROM oven/bun:1 AS build
WORKDIR /app
COPY apps/control-plane/web/package.json apps/control-plane/web/bun.lock ./
RUN bun install --frozen-lockfile
COPY apps/control-plane/web/ ./
RUN bun run build
FROM nginxinc/nginx-unprivileged:1.29-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY packages/embed-sdk/loader.js /usr/share/nginx/html/sdk/v1/loader.js
COPY apps/control-plane/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
