FROM node:25-bookworm

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates build-essential \
        git ripgrep fd-find \
        python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --break-system-packages uv ruff basedpyright
RUN npm install -g opencode-ai@latest @openchamber/web@latest

RUN userdel -r node 2>/dev/null || true \
    && useradd -u 1000 -m -s /bin/bash user

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER user
WORKDIR /home/user

EXPOSE 4096

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
