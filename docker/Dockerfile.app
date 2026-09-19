FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

WORKDIR /app/

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

EXPOSE 8123

CMD ["uv", "run", "--no-sync", "python", "main.py"]
