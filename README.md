# Airport

FastAPI-Dienst, der pro Browser-Session einen `opencode-plane`-Container startet.
Der Client greift direkt per iframe auf OpenCode Web zu.
Schließt der Tab → stoppt der Container automatisch.

> **Airport** koordiniert, **Planes** fliegen. Der FastAPI-Prozess ist der Airport;
> jede OpenCode-Web-Instanz im Docker-Container ist ein Plane.

## Wie es funktioniert

```
Browser öffnet /  →  index.html lädt

index.html:  POST /session
             ↓ Container startet + wartet bis bereit (max. 60 s)
             ↓ {"token": "…", "url": "http://HOST:PORT/"}

index.html:  WebSocket ws://fastapi/session/{token}/lifecycle  öffnen
             ↓ WS offen = Container läuft
             ↓ WS zu    = Container stoppt

index.html:  <iframe src="url"> → OpenCode Web direkt aus Container
```

## Voraussetzungen

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Docker (auf dem Host)

## Setup

```bash
cp .env.example .env
# .env anpassen: OPENCODE_IMAGE, PUBLIC_HOST, OLLAMA_BASE_URL, OLLAMA_MODEL

uv sync
```

## Starten

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Entwicklung mit Auto-Reload:

```bash
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Plane-Image bauen

```bash
docker build -t opencode-plane:latest docker/opencode/
```

## Konfiguration (.env)

| Variable               | Default           | Beschreibung                                          |
|------------------------|-------------------|-------------------------------------------------------|
| `OPENCODE_IMAGE`       | `opencode-plane:latest` | Plane-Image (OpenCode Web Container)            |
| `MAX_CONTAINERS`       | `5`               | Max gleichzeitige Sessions                            |
| `CONTAINER_MEMORY`     | `3g`              | RAM-Limit pro Container                               |
| `CONTAINER_CPUS`       | `1.0`             | CPU-Quota pro Container                               |
| `CONTAINER_TMPFS_SIZE` | `2g`              | Home-Größe (`/home/user` als tmpfs)                   |
| `OPENCODE_PORT`        | `4096`            | Port, auf dem OpenCode im Container lauscht           |
| `PUBLIC_HOST`          | `localhost`       | Adresse erreichbar vom Client-Browser                 |
| `OLLAMA_BASE_URL`      | `http://host.docker.internal:11434/v1` | OpenAI-kompatible Ollama-URL (vom Container aus erreichbar) |
| `OLLAMA_MODEL`         | `gemma4:e4b`      | In OpenCode ausgewähltes Ollama-Modell                |

> `PUBLIC_HOST` muss die Adresse sein, die der **Browser** des Clients erreichen kann.
> Lokal: `localhost`. Auf einem Server: öffentliche IP oder Hostname.

### Ollama (lokal)

Jeder OpenCode-Container wird beim Start mit einer generierten `opencode.json`
konfiguriert, die als einzigen Provider eine lokale Ollama-Instanz einträgt
(OpenAI-kompatibel via `/v1`). Werte kommen aus `OLLAMA_BASE_URL` und
`OLLAMA_MODEL`.

Damit der Container den Host erreicht, setzt `docker.py` automatisch
`--add-host=host.docker.internal:host-gateway`. Voraussetzung: Ollama läuft
auf dem Host und lauscht auf `11434` (`ollama serve`). Modell muss gezogen
sein: `ollama pull gemma4:e4b` (bzw. das in `.env` konfigurierte Modell).

## API

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/` | GET | Landing Page (index.html) |
| `/health` | GET | `{"status": "ok"}` |
| `/status` | GET | `{"open": 2, "free": 3, "max": 5}` |
| `/session` | POST | Container starten → `{"token": "…", "url": "…"}` / 503 bei Limit |
| `/session/{token}/lifecycle` | WS | Lifecycle-Verbindung; Disconnect = Container stop |

## Projektstruktur

```
app/
├── main.py                   # FastAPI App, Lifespan, /health
├── config.py                 # Pydantic Settings aus .env
├── services/
│   ├── docker.py             # Container start/stop + health-wait (60 s)
│   └── session.py            # SessionManager: State, Limit, Shutdown-Cleanup
├── routers/
│   ├── session.py            # POST /session, GET /status
│   └── lifecycle.py          # WS /session/{token}/lifecycle
└── static/
    └── index.html            # Landing Page: iframe + Lifecycle-WS
docker/
└── opencode/
    ├── Dockerfile            # OpenCode-Container (ubuntu:25.10 + opencode-ai)
    └── entrypoint.sh         # Generiert opencode.json aus OLLAMA_*-Env-Vars
```
