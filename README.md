<p align="center">
  <img src="app/static/logo.png" alt="Mayfly Logo" width="200"/>
</p>

---

![Python](https://img.shields.io/badge/python-3.13+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688)
![Docker](https://img.shields.io/badge/Docker-7.0+-2496ED)
![MCP](https://img.shields.io/badge/MCP-FastMCP-8A2BE2)
![Status](https://img.shields.io/badge/status-alpha-orange)

> Ephemere OpenCode-Container pro Browser-Session. Tab zu → Container weg.

## ⚡ Setup

```bash
cp .env.example .env
uv sync
docker build -t opencode-mayfly:latest docker/opencode/
```

## ▶ Start

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

↻ Auto-Reload: `--reload` anhängen.

## ⚙ Konfiguration (`.env`)

| Variable | |
|---|---|
| `OPENCODE_IMAGE` | Container-Image |
| `MAX_CONTAINERS` | Max Sessions |
| `CONTAINER_MEMORY` | RAM je Container |
| `CONTAINER_CPUS` | CPU je Container |
| `CONTAINER_TMPFS_SIZE` | `/home/user` tmpfs |
| `OPENCODE_PORT` | Port im Container |
| `PUBLIC_HOST` | vom Browser erreichbare Host-Adresse |
| `OPENAI_BASE_URL` | OpenAI-kompatible API |
| `OPENAI_MODEL` | Modell-Name |
| `OPENAI_CONTEXT_SIZE` | Context-Window |
| `OPENAI_OUTPUT_SIZE` | Max Output-Tokens |

## 🛰 API

| | | |
|---|---|---|
| `GET`  | `/`                          | Landing Page |
| `GET`  | `/status`                    | `{open, free, max}` |
| `POST` | `/session`                   | Container starten → `{token, url}` · 503 bei Limit |
| `WS`   | `/session/{token}/lifecycle` | Disconnect ⇒ Container stop |
| `*`    | `/mcp`                       | Streamable-HTTP MCP |
