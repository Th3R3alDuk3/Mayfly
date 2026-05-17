<div align="center">
  <img src="app/static/logo.png" alt="Mayfly" width="180"/>
  <h1>Mayfly</h1>
  <p><em>Disposable, browser-based coding sessions in isolated Docker sandboxes.</em></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=white" alt="">
    <img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white" alt="">
    <img src="https://img.shields.io/badge/Docker-required-2496ED?logo=docker&logoColor=white" alt="">
    <img src="https://img.shields.io/badge/status-alpha-E48400" alt="">
  </p>
</div>

Mayfly starts short-lived OpenChamber workspaces backed by OpenCode. Each browser session gets its own Docker sandbox, its own workspace, and its own OpenChamber UI password.

The sandboxes are intentionally disposable: no local editor setup, no persistent sandbox state, and automatic cleanup after disconnect.

## 🛠️ How It Works

```mermaid
flowchart LR
  B([Browser]) -- HTTP + WS --> A[FastAPI app]
  A -- reverse proxy --> M[[Mayfly sandbox<br/>OpenCode + OpenChamber]]
  A -- Docker API --> M
```

- The FastAPI app creates and tracks sessions.
- Each session starts one sandbox container on the shared `mayfly-net` Docker network — sandboxes have **no host port mapping**.
- The browser visits `/view/{token}`, gets a session cookie, and loads OpenChamber via a same-origin reverse proxy at `/mayfly/`.
- The reverse proxy is a small in-house module ([`app/services/proxy.py`](app/services/proxy.py), HTTP via `httpx` + WS via `websockets`) — no third-party proxy dependency.
- A lifecycle WebSocket (`/sessions/{token}/lifecycle`) signals tab close so the sandbox is torn down after a short delay.
- `$HOME` and `/tmp` inside the sandbox are tmpfs mounts, so every new session starts clean.
- `docker/entrypoint.sh` generates OpenCode config, OpenChamber settings, the workspace directory, and a small `AGENTS.md`.

## 📋 Requirements

- Docker with access to `/var/run/docker.sock`
- Python 3.13+ for local development
- An OpenAI-compatible model endpoint reachable from the sandbox, for example Ollama, vLLM, llama.cpp, or LM Studio

For model services running on the Docker host, use `host.docker.internal` in `.env`.

## 🚀 Quick Start

```bash
cp .env.example .env
docker compose --profile build build
docker compose up -d
```

Open <http://localhost:8123>.

When changing files under `docker/`, rebuild the sandbox image before starting new sessions:

```bash
docker compose --profile build build mayfly
```

## ⚙️ Configuration

All runtime configuration lives in [.env](.env.example).

| Variable | Purpose |
| --- | --- |
| `PUBLIC_URL` | external base URL (with scheme) used in API/MCP session links |
| `APP_PORT`, `APP_BIND_HOST` | FastAPI port and bind address |
| `TZ` | timezone applied to the app and every sandbox |
| `MAYFLY_IMAGE` | per-session sandbox image |
| `MAYFLY_MAX_SESSIONS` | max concurrent sessions |
| `MAYFLY_MEMORY`, `MAYFLY_CPUS` | per-sandbox resource limits |
| `MAYFLY_HOME_SIZE`, `MAYFLY_TMP_SIZE` | sandbox `$HOME` and `/tmp` tmpfs sizes |
| `MAYFLY_WORKSPACE_DIR` | workspace directory inside the sandbox home |
| `MAYFLY_UPLOAD_LIMIT` | max upload size into the workspace |
| `MAYFLY_CONNECT_TIMEOUT`, `MAYFLY_DISCONNECT_TIMEOUT` | cleanup timing for unused sessions |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` | model endpoint passed to OpenCode |
| `OPENAI_CONTEXT_TOKENS`, `OPENAI_OUTPUT_TOKENS` | model limits passed to OpenCode |
| `OPENAI_TIMEOUT`, `OPENAI_CHUNK_TIMEOUT` | OpenCode provider request timeouts |

For offline or Nexus builds, override `PIP_INDEX_URL`, `PIP_TRUSTED_HOST`, `NPM_REGISTRY`, and `NPM_STRICT_SSL`.

## 🔌 API Surface

- `GET /` - browser entrypoint
- `POST /view` - create a browser session and redirect to `/view/{token}`
- `GET /view/{token}` - browser view for one session (sets `mayfly_session` cookie)
- `POST /sessions` - create a session via API, returns `url` and `password`
- `GET /sessions/status` - active, available, limit, and memory usage; pass `?token=<t>` for a single session
- `POST /sessions/{token}/upload` - password-protected file upload into the workspace
- `DELETE /sessions/{token}` - stop a session
- `WS /sessions/{token}/lifecycle` - browser lifecycle channel (drives the disconnect timeout)
- any unmatched path - reverse-proxied to the sandbox identified by the `mayfly_session` cookie (the browser iframe loads `/mayfly/` by convention)
- `/docs` - OpenAPI docs
- `/mcp/` - MCP endpoint

Static UI assets are served under `/static/*`; `/favicon.ico` serves the Mayfly logo.

The cookie-routed proxy means **one active OpenChamber session per browser origin**. Opening a second tab on the same origin overwrites the cookie and points API/WS traffic at the new session. The first tab detects this via a `BroadcastChannel('mayfly-session')` claim, hides its iframe, and closes its lifecycle WebSocket — that triggers the regular disconnect cleanup for the displaced container.

## 🛡️ Security Model

Sandbox containers run as an unprivileged user with a read-only root filesystem, dropped Linux capabilities, `no-new-privileges`, PID/memory/CPU limits, and tmpfs mounts for writable runtime state. Sandboxes have no host port mapping — they are only reachable through the app's reverse proxy.

Mayfly is still alpha. If you bind the app to a public interface, put it behind trusted network controls.

## 🧩 Layout

- [`app/`](app/) - FastAPI app, routers, services, templates, and static UI assets
- [`docker/Dockerfile.app`](docker/Dockerfile.app) - orchestrator image
- [`docker/Dockerfile.mayfly`](docker/Dockerfile.mayfly) - per-session sandbox image
- [`docker/entrypoint.sh`](docker/entrypoint.sh) - sandbox startup config for OpenCode, OpenChamber, and workspace instructions
- [`docker-compose.yml`](docker-compose.yml) - app service, build profile, and shared Docker network
