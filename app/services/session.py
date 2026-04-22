from asyncio import (
    CancelledError,
    Lock,
    Task,
    create_task,
    gather,
    get_running_loop,
    run_coroutine_threadsafe,
    sleep,
)
import logging
from threading import Event as ThreadEvent, Thread
from typing import Any

from docker import from_env
from docker.client import DockerClient

from app.config import Settings
from app.models.docker import ContainerInfo
from app.models.session import Session
from app.services.docker import start_container, stop_container

log = logging.getLogger(__name__)

_CONTAINER_NAME_PREFIX = "opencode-"
_DEAD_ACTIONS = frozenset({"die", "oom", "health_status: unhealthy"})


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._abandon_tasks: dict[str, Task[None]] = {}
        self._lock = Lock()
        self._loop = get_running_loop()
        self._events_shutdown = ThreadEvent()
        self._events_client: DockerClient = from_env()
        self._events_thread = Thread(
            target=self._watch_events,
            name="mayfly-docker-events",
            daemon=True,
        )
        self._events_thread.start()

    async def create(self) -> Session:
        async with self._lock:
            if len(self._sessions) >= self._settings.max_containers:
                raise RuntimeError("Max container limit reached")
            session = Session()
            self._sessions[session.token] = session

        try:
            info: ContainerInfo = await start_container(session.token, self._settings)
        except Exception:
            async with self._lock:
                self._sessions.pop(session.token, None)
            raise

        session.container_info = info
        self._abandon_tasks[session.token] = create_task(
            self._abandon_timeout(session.token)
        )
        return session

    def _watch_events(self) -> None:
        try:
            for event in self._events_client.events(decode=True, filters={"type": "container"}):
                if self._events_shutdown.is_set():
                    return
                self._dispatch_event(event)
        except Exception:
            if not self._events_shutdown.is_set():
                log.exception("Docker event watcher crashed")

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        name = event.get("Actor", {}).get("Attributes", {}).get("name", "").lstrip("/")
        if not name.startswith(_CONTAINER_NAME_PREFIX):
            return
        action = event.get("Action", "")
        if action not in _DEAD_ACTIONS:
            return
        token = name[len(_CONTAINER_NAME_PREFIX):]
        run_coroutine_threadsafe(self._on_container_gone(token, action), self._loop)

    async def _on_container_gone(self, token: str, reason: str) -> None:
        if token not in self._sessions:
            return
        log.warning("Container for session %s gone (%s) — closing", token, reason)
        await self.close(token)

    async def _abandon_timeout(self, token: str) -> None:
        try:
            await sleep(self._settings.abandon_timeout_seconds)
        except CancelledError:
            return
        self._abandon_tasks.pop(token, None)
        log.warning("Session %s abandoned — no lifecycle WS connected, closing", token)
        await self.close(token)

    async def confirm_connected(self, token: str) -> bool:
        async with self._lock:
            if token not in self._sessions:
                return False
            task = self._abandon_tasks.pop(token, None)
        if task:
            task.cancel()
        return True

    async def close(self, token: str) -> None:
        async with self._lock:
            session = self._sessions.pop(token, None)
            task = self._abandon_tasks.pop(token, None)
        if task:
            task.cancel()
        if session and session.container_info:
            await stop_container(session.container_info.id)

    def get(self, token: str) -> Session | None:
        return self._sessions.get(token)

    def status(self) -> dict[str, int]:
        open_count = len(self._sessions)
        return {
            "open": open_count,
            "free": max(0, self._settings.max_containers - open_count),
            "max": self._settings.max_containers,
        }

    async def close_all(self) -> None:
        self._events_shutdown.set()
        try:
            self._events_client.close()
        except Exception:
            log.exception("Failed to close Docker events client")
        tokens = list(self._sessions.keys())
        log.info("Shutting down %d session(s)", len(tokens))
        await gather(*(self.close(t) for t in tokens))
