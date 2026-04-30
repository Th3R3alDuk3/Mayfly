FROM ghcr.io/astral-sh/uv:0.5-python3.13-bookworm-slim AS builder

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_INDEX_URL=${PIP_INDEX_URL} \
    UV_INSECURE_HOST=${PIP_TRUSTED_HOST}

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --no-install-project --no-dev


FROM python:3.13-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ARG APP_PORT
ENV APP_PORT=${APP_PORT}

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY app /app/app

EXPOSE ${APP_PORT}

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT}"]
