<p align="center">
  <img src="app/static/logo.png" alt="Mayfly Logo" width="200"/>
</p>

---

![Python](https://img.shields.io/badge/python-3.13+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688)
![Docker SDK](https://img.shields.io/badge/Docker%20SDK-7.0+-2496ED)
![Status](https://img.shields.io/badge/status-alpha-orange)

> Disposable browser-based coding sessions in isolated Docker containers.

## What

A small FastAPI service that hands out short-lived `OpenCode` / `OpenChamber` workspaces. Each session gets its own container and its own subdomain — and disappears when nobody's looking.

Caddy fronts everything for TLS and per-session subdomain routing.

## Run

```bash
cp .env.example .env
uv sync
docker compose --profile build build
docker compose up -d
uv run uvicorn app.main:app --port 8123
```

Then open `https://mayfly.localhost:8443/`.

## More

Configuration lives in `.env`. API surface is small — OpenAPI at `/docs`, MCP at `/mcp/`.
