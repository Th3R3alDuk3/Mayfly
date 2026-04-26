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

A small FastAPI service that hands out short-lived `OpenCode` / `OpenChamber` workspaces. Each session gets its own mayfly and its own subdomain — and disappears when nobody's looking.

Caddy fronts everything for TLS and per-session subdomain routing.

## Run

```bash
cp .env.example .env
docker compose --profile build build   # mayfly sandbox image
docker compose up -d --build           # app + caddy
```

Then open `https://mayfly.localhost:8443/`.

## Layout

- `app` — FastAPI orchestrator (built from [docker/Dockerfile.app](docker/Dockerfile.app))
- `mayfly` — per-session sandbox image with OpenCode/OpenChamber (built from [docker/Dockerfile.mayfly](docker/Dockerfile.mayfly), build-only profile)
- `caddy` — TLS + per-session subdomain routing

## More

Configuration lives in `.env`. API surface is small — OpenAPI at `/docs`, MCP at `/mcp/`.

### Reaching the MCP / API endpoint

The `app` service binds to `172.17.0.1:${APP_PORT}` — the default Docker bridge gateway. That single bind serves all three reasonable callers:

| Caller | URL |
| --- | --- |
| Browser / TLS-aware client on the host | `https://mayfly.localhost:${PUBLIC_PORT}/mcp/` (via Caddy) |
| Plain HTTP from the host | `http://172.17.0.1:${APP_PORT}/mcp/` |
| Another container on the default `bridge` network (e.g. OpenWebUI) | `http://172.17.0.1:${APP_PORT}/mcp/` |

The HTTP route stays off your LAN (no `0.0.0.0` bind) and you don't need to attach foreign containers to `mayfly-net`.
