<p align="center">
  <img src="app/static/logo.png" alt="Mayfly Logo" width="200"/>
</p>

---

![Python](https://img.shields.io/badge/python-3.13+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688)
![Docker SDK](https://img.shields.io/badge/Docker%20SDK-7.0+-2496ED)
![Status](https://img.shields.io/badge/status-alpha-orange)

> Mayfly runs temporary browser-based `OpenCode`/`OpenChamber` sessions in isolated Docker containers.

## Overview

Mayfly is a small FastAPI service for disposable coding workspaces. It starts one container per browser session and removes it again when the session is no longer active.

The service also exposes a small HTTP API and an MCP endpoint for external clients.

## Setup

```bash
cp .env.example .env
```

```bash
uv sync
```

```bash
docker build -t mayfly:0.1.0 docker/
```

The Docker tag must match `DOCKER_IMAGE` in `.env`.

## Start

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8123
```

For local development:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8123 --reload
```

## Configuration

Runtime settings live in `.env`. Use `.env.example` as the template and adjust host, port, Docker, container limits, and model API settings as needed.

`PUBLIC_PORT` should match the browser-reachable Mayfly port.

## API

Mayfly exposes a small API:

| Method | Path |
|---|---|
| `GET` | `/` |
| `POST` | `/sessions` |
| `DELETE` | `/sessions/{token}` |
| `GET` | `/sessions/status` |
| `GET` | `/view/{token}` |
| `WS` | `/sessions/{token}/lifecycle` |
| `POST` | `/mcp/` |
