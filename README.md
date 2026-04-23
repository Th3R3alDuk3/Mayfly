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
docker build -t opencode-mayfly:0.1.0 docker/opencode/
```

Das Build-Tag muss zu `DOCKER_IMAGE` in `.env` passen.

## ▶ Start

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

↻ Auto-Reload: `--reload` anhängen.

## ↺ Ablauf

1. `POST /session` erstellt eine Session und startet einen dedizierten OpenCode-Container.
2. Das Frontend verbindet sich danach mit `WS /session/{token}/lifecycle`.
3. Wenn die WebSocket-Verbindung endet oder gar nicht innerhalb von `CONTAINER_TIMEOUT` zustande kommt, wird die Session geschlossen und der Container entfernt.

Beim App-Shutdown räumt Mayfly alle noch offenen Sessions ebenfalls weg.

## ⚙ Konfiguration (`.env`)

| Variable | |
|---|---|
| `DOCKER_IMAGE` | Container-Image |
| `DOCKER_PORT` | Port im Container |
| `MAX_CONTAINERS` | Max Sessions |
| `CONTAINER_MEMORY` | RAM je Container |
| `CONTAINER_CPUS` | CPU je Container |
| `CONTAINER_TMPFS_SIZE` | `/home/user` tmpfs |
| `CONTAINER_TIMEOUT` | Sekunden bis zum Abbruch, falls keine Lifecycle-WS verbunden wird |
| `OPENAI_BASE_URL` | OpenAI-kompatible API |
| `OPENAI_MODEL` | Modell-Name |
| `OPENAI_CONTEXT_SIZE` | Context-Window |
| `OPENAI_OUTPUT_SIZE` | Max Output-Tokens |

Standardwerte stehen in `.env.example`.

## 🐳 Docker-Image

Das Container-Image installiert derzeit:

| Paket | Version |
|---|---|
| `opencode-ai` | `1.14.22` |
| `@openchamber/web` | `1.9.8` |

## 🛰 API

| | | |
|---|---|---|
| `GET`  | `/`                          | Landing Page |
| `GET`  | `/status`                    | `{open, free, max}` |
| `POST` | `/session`                   | Container starten → `{token, url}` · `503` bei Limit oder Startfehler |
| `WS`   | `/session/{token}/lifecycle` | Verbindungsende ⇒ Session schließen und Container stoppen |
| `*`    | `/mcp`                       | Streamable-HTTP MCP |
