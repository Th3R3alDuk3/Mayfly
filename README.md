<div align="center">
  <img src="app/static/logo.png" alt="Mayfly" width="180"/>
  <h1>Mayfly</h1>
  <p><em>Disposable, browser-based coding sessions in isolated Docker sandboxes.</em></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.13+-3776AB?logo=python&logoColor=white" alt="">
    <img src="https://img.shields.io/badge/FastAPI-0.115-009688?logo=fastapi&logoColor=white" alt="">
    <img src="https://img.shields.io/badge/Docker-required-2496ED?logo=docker&logoColor=white" alt="">
    <img src="https://img.shields.io/badge/status-alpha-E48400" alt="">
  </p>
</div>

---

One click and you're in a fresh [OpenCode](https://opencode.ai) workspace running inside its own short-lived sandbox. No install, no leftover state — gone when you close the tab.

## 🌊 Flow

```mermaid
flowchart LR
  B([Browser]) -- HTTP --> A[FastAPI orchestrator]
  A -- docker run --> M[[mayfly sandbox<br/>OpenCode + OpenChamber]]
  B -. iframe<br/>:configured port range .-> M
```

## 🚀 Quick start

You need an OpenAI-compatible inference endpoint reachable from the host (e.g. vLLM, llama.cpp, Ollama, LM Studio, or the OpenAI API itself). Point `OPENAI_BASE_URL` / `OPENAI_MODEL` at it in `.env`.

```bash
cp .env.example .env
docker compose --profile build build   # build the sandbox image
docker compose up -d                   # start the orchestrator
```

Open <http://localhost:8123>.

## ⚙️ Tunables

Everything lives in [.env](.env.example). The ones worth knowing:

| | |
| --- | --- |
| `MAYFLY_MAX_SESSIONS` | concurrency cap |
| `MAYFLY_HOST_PORT_START` / `MAYFLY_HOST_PORT_END` | inclusive host port range for sandbox sessions |
| `MAYFLY_CONNECT_TIMEOUT` | how long a session waits for the first browser connect |
| `MAYFLY_DISCONNECT_TIMEOUT` | how long an unwatched session survives |
| `OPENAI_BASE_URL` / `OPENAI_MODEL` | LLM each sandbox talks to |
| `OPENAI_TIMEOUT` / `OPENAI_CHUNK_TIMEOUT` | be generous with slow / cold models |
| `TZ` | shared timezone for app + sandboxes |

Offline / Nexus builds: override `PIP_INDEX_URL` and `NPM_REGISTRY` in `.env`.

## 🌐 Surface

- **`/`** — start a session
- **`/docs`** — OpenAPI
- **`/mcp/`** — MCP endpoint, reachable from the host at `http://localhost:8123/mcp/`

## 🧩 Layout

- [`app/`](app/) — FastAPI orchestrator
- [`docker/Dockerfile.mayfly`](docker/Dockerfile.mayfly) — per-session sandbox image (build-only profile)
- [`docker/Dockerfile.app`](docker/Dockerfile.app) — orchestrator image
