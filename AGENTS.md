# AGENTS.md — Airport

## Was dieses Projekt tut

**Airport** ist ein FastAPI-Prozess, der direkt auf dem Host läuft und per
Docker-SDK **Planes** (`opencode-plane`-Container mit OpenCode Web) verwaltet —
ein Plane pro Browser-Session.

---

## Ablauf einer Session

```
1. Browser öffnet GET /
   → FastAPI liefert app/static/index.html aus

2. index.html: POST /session
   → SessionManager reserviert Slot (Limit-Prüfung gegen MAX_CONTAINERS)
   → docker.start_container() startet Container mit zufällig gepublishtem Port
   → _wait_ready() pollt http://localhost:{port}/ alle 2 s, max. 60 s
   → Erst wenn HTTP 200 kommt, gibt POST /session zurück: {token, url}

3. index.html: WebSocket ws://fastapi/session/{token}/lifecycle öffnen
   → Verbindung bleibt offen solange Tab offen ist

4. index.html: <iframe src="{url}">
   → Browser greift DIREKT auf Container zu (kein Proxy durch FastAPI)
   → OpenCode Web läuft vollständig im iframe

5. Tab schließen
   → WS bricht ab → lifecycle_ws finally-Block → manager.close(token)
   → container.stop() + container.remove()

6. FastAPI shutdown (SIGTERM)
   → Lifespan-Cleanup → manager.close_all() → alle Container gestoppt
```

---

## Modulverantwortlichkeiten

### `app/config.py`
Pydantic `BaseSettings`, liest `.env`. Singleton über `@lru_cache`.

Felder: `opencode_image`, `max_containers`, `container_memory`, `container_cpus`,
`container_tmpfs_size`, `opencode_port`, `public_host`, `ollama_base_url`,
`ollama_model`.

### `app/services/docker.py`
Synchrones Docker-SDK, in `asyncio.to_thread()` gewickelt.

- `start_container(session_id, settings) → ContainerInfo`
  - Startet Container: `user=1000:1000`, `mem_limit`, `nano_cpus`, `tmpfs /home/user` + `/tmp`
    (beide mit `exec`, damit Bun native `.so` dlopen'en kann),
    `ports={OPENCODE_PORT/tcp: None}` (random Host-Port), `auto_remove=False`
  - Setzt `extra_hosts={"host.docker.internal": "host-gateway"}` — Container kann
    Ollama auf dem Host erreichen
  - Gibt `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `OPENCODE_PORT` als Env in den Container
  - Liest zugewiesenen Host-Port aus `container.ports`
  - Ruft `_wait_ready(host_port)` auf — blockiert bis HTTP 200 oder TimeoutError
  - Gibt `ContainerInfo(container_id, host_port)` zurück
- `stop_container(container_id)` — `stop(timeout=5)` + `remove()`, schluckt `NotFound`
- `_wait_ready(host_port)` — pollt `http://localhost:{port}/`, 2 s Intervall, 60 s Timeout

### `app/services/session.py`
In-Memory-State: `dict[token → Session]`, geschützt durch `asyncio.Lock`.

- `Session` — dataclass: `token`, `container_id`, `host_port`, `created_at`
- `SessionManager.create()` — reserviert Slot mit leerem Placeholder, startet Container,
  ersetzt Placeholder durch echte Session
- `SessionManager.close(token)` — popt aus Dict, stoppt Container
- `SessionManager.get(token)` — lookup ohne Lock (read-only, ausreichend für Python GIL)
- `SessionManager.status()` — `{open, free, max}`
- `SessionManager.close_all()` — parallel via `asyncio.gather`

### `app/routers/session.py`
- `POST /session` → 201 + `{token, url}` | 503 wenn Limit erreicht
- `GET /status` → `{open, free, max}`

URL-Format: `http://{PUBLIC_HOST}:{host_port}/`

### `app/routers/lifecycle.py`
`WS /session/{token}/lifecycle`
- Unbekannter Token → close mit Code 4004
- Accepted → loop `receive_text()` bis Disconnect/Exception
- `finally` → `manager.close(token)`

### `app/main.py`
- Lifespan: `SessionManager` anlegen → yield → `close_all()`
- Routers: `session`, `lifecycle`
- `GET /health` → `{"status": "ok"}`
- `StaticFiles` Mount auf `/` (nach API-Routen, damit diese Vorrang haben)

### `docker/opencode/Dockerfile` + `entrypoint.sh`
OpenCode-Web-Image (ubuntu:25.10 mit Node 20 aus Default-Apt, `npm install -g opencode-ai@latest`,
User `user` mit UID 1000, `WORKDIR /home/user`). `entrypoint.sh` generiert beim Container-Start
`opencode.json` unter `$HOME/.config/opencode/` mit einem Ollama-Provider (OpenAI-kompatibel
via `/v1`). Werte aus Env: `OLLAMA_BASE_URL`, `OLLAMA_MODEL`. Anschließend `cd "${HOME}"` und
`exec opencode web`.

### `app/static/index.html`
- Spinner + Fehler-Box (dark theme)
- `POST /session` → wartet (Container-Start dauert) → bei Fehler: Meldung anzeigen
- WS öffnen (`ws://` oder `wss://` je nach Protokoll)
- `<iframe>` auf Container-URL setzen

---

## Container-Konfiguration (pro Session)

| Parameter | Wert |
|-----------|------|
| User | `1000:1000` |
| Memory | `CONTAINER_MEMORY` (default `3g`) |
| CPU | `CONTAINER_CPUS × 10⁹` Nano-CPUs |
| Home | tmpfs `/home/user`, Größe `CONTAINER_TMPFS_SIZE` (plus `/tmp` tmpfs 64m), beide `exec` |
| Port | random gepublisht auf Host (`0.0.0.0`) |
| Name | `opencode-{token}` |
| auto_remove | `False` (manuelles Cleanup) |

---

## Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `OPENCODE_IMAGE` | `opencode-plane:latest` | Plane-Image (OpenCode Web Container) |
| `MAX_CONTAINERS` | `5` | Max gleichzeitige Sessions |
| `CONTAINER_MEMORY` | `3g` | RAM-Limit |
| `CONTAINER_CPUS` | `1.0` | CPU-Cores |
| `CONTAINER_TMPFS_SIZE` | `2g` | Home-Größe (`/home/user` tmpfs) |
| `OPENCODE_PORT` | `4096` | Port im Container |
| `PUBLIC_HOST` | `localhost` | Adresse für Client-URLs (muss vom Browser erreichbar sein) |
| `OLLAMA_BASE_URL` | `http://host.docker.internal:11434/v1` | OpenAI-kompatible Ollama-URL (vom Container aus) |
| `OLLAMA_MODEL` | `gemma4:e4b` | Modell-Name, der in `opencode.json` eingetragen wird |

---

## Konventionen

- Python 3.13+, uv, ruff (`line-length = 120`, `target-version = "py313"`)
- Typ-Annotationen überall, kein `print()`, kein bare `except:`
- Docker-SDK-Calls immer in `asyncio.to_thread()` (sync SDK, async Event-Loop)
- Max 500 Zeilen pro Datei, 120 Zeichen pro Zeile

## Starten

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```
