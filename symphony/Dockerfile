FROM node:22-bookworm-slim
ARG CODEX_VERSION=0.154.0
ARG SYMPHONY_SHA256=bace377e92b7c244a29da15a88f517c7722ca69e249068395f77ce6912ef39eb
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git gh python3 python3-venv python3-yaml build-essential ripgrep openssh-client libssl3 libncurses6 libstdc++6 && rm -rf /var/lib/apt/lists/*
RUN npm install -g @openai/codex@${CODEX_VERSION} && npm cache clean --force
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /usr/local/bin/
RUN curl -fL --retry 3 https://github.com/openai/symphony/releases/download/v0.0.2/symphony-v0.0.2-linux_x86_64 -o /usr/local/bin/symphony && echo "${SYMPHONY_SHA256}  /usr/local/bin/symphony" | sha256sum -c - && chmod 755 /usr/local/bin/symphony
RUN npm install -g bun@1.3.10 && npx --yes playwright@1.61.1 install-deps chromium webkit
COPY symphony/ /opt/symphony/
COPY services/ /opt/platform/services/
COPY projects/ /opt/platform/projects/
RUN mkdir -p /home/node/workspaces /home/node/.codex /home/node/.cache /home/node/symphony-runtime /home/node/logs && chown -R node:node /home/node
USER node
ENV HOME=/home/node CODEX_HOME=/home/node/.codex SYMPHONY_INSTALL_DIR=/home/node/symphony-runtime ERL_FLAGS="+S 2:2" UV_CACHE_DIR=/home/node/.cache/uv
WORKDIR /home/node
ENTRYPOINT ["bash", "/opt/symphony/entrypoint.sh"]
