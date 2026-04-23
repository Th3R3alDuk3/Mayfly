FROM node:25-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g opencode-ai@latest @openchamber/web@latest

RUN userdel -r node 2>/dev/null || true \
    && useradd -u 1000 -m -s /bin/bash user

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER user
WORKDIR /home/user

EXPOSE 4096

HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=12 \
    CMD curl -sf http://localhost:4096/ || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
