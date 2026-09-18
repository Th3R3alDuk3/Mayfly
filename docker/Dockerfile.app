FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=

ENV UV_INDEX_URL=${PIP_INDEX_URL} \
    UV_INSECURE_HOST=${PIP_TRUSTED_HOST} \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

EXPOSE 8123

CMD ["uv", "run", "--no-sync", "python", "main.py"]
