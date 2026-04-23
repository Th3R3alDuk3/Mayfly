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
from dataclasses import dataclass
from logging import getLogger
from threading import Event as ThreadEvent, Thread
from typing import Any

from app.config import Settings
from app.models.session import Session
from app.services.docker import DockerRuntime


logger = getLogger(__name__)

_CONTAINER_NAME_PREFIX = "opencode-"
_DEAD_ACTIONS = frozenset({"die", "oom", "health_status: unhealthy"})


@dataclass(slots=True)
class ManagedSession:
    session: Session
    abandon_task: Task[None] | None = None
    close_task: Task[None] | None = None


def _cancel_task(task: Task[None] | None) -> None:
    if task:
        task.cancel()


def _dead_container_event(event: dict[str, Any]) -> tuple[str, str] | None:
    name = event.get("Actor", {}).get("Attributes", {}).get("name", "").lstrip("/")
    if not name.startswith(_CONTAINER_NAME_PREFIX):
        return None

    action = event.get("Action", "")
    if action not in _DEAD_ACTIONS:
        return None

    token = name[len(_CONTAINER_NAME_PREFIX):]
    return token, action


class SessionManager:
    def __init__(self, settings: Settings, runtime: DockerRuntime | None = None) -> None:
        self._settings = settings
        self._runtime = runtime or DockerRuntime(settings)
        self._sessions: dict[str, ManagedSession] = {}
        self._lock = Lock()
        self._loop = get_running_loop()
        self._events_shutdown = ThreadEvent()
        self._events_thread = Thread(
            target=self._watch_events,
            name="mayfly-docker-events",
            daemon=True,
        )
        self._events_thread.start()

    async def create(self) -> Session:
        entry = ManagedSession(session=Session())
        token = entry.session.token

        async with self._lock:
            if len(self._sessions) >= self._settings.max_containers:
                raise RuntimeError("Max container limit reached")
            self._sessions[token] = entry

        try:
            entry.session.container_info = await self._runtime.start_session_container(token)
        except Exception:
            async with self._lock:
                self._sessions.pop(token, None)
            raise

        entry.abandon_task = create_task(self._abandon_timeout(token))
        return entry.session

    def _watch_events(self) -> None:
        try:
            for event in self._runtime.iter_container_events():
                if self._events_shutdown.is_set():
                    return
                self._dispatch_event(event)
        except Exception:
            if not self._events_shutdown.is_set():
                logger.exception("Docker event watcher crashed")

    def _dispatch_event(self, event: dict[str, Any]) -> None:
        dead_event = _dead_container_event(event)
        if dead_event is None:
            return

        token, action = dead_event
        run_coroutine_threadsafe(self._on_container_gone(token, action), self._loop)

    async def _on_container_gone(self, token: str, reason: str) -> None:
        async with self._lock:
            if token not in self._sessions:
                return

        logger.warning("Container for session %s gone (%s) — closing", token, reason)
        await self.close(token)

    async def _abandon_timeout(self, token: str) -> None:
        try:
            await sleep(self._settings.container_timeout)
        except CancelledError:
            return

        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return
            entry.abandon_task = None

        logger.warning("Session %s abandoned — no lifecycle WS connected, closing", token)
        await self.close(token)

    async def confirm_connected(self, token: str) -> bool:
        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None or entry.close_task is not None:
                return False

            abandon_task = entry.abandon_task
            entry.abandon_task = None

        _cancel_task(abandon_task)
        return True

    async def close(self, token: str) -> None:
        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return

            if entry.close_task is None:
                entry.close_task = create_task(self._close_session(token, entry))

            close_task = entry.close_task

        await close_task

    async def _close_session(self, token: str, entry: ManagedSession) -> None:
        async with self._lock:
            current = self._sessions.get(token)
            if current is not entry:
                return

            abandon_task = current.abandon_task
            current.abandon_task = None

        _cancel_task(abandon_task)

        cleanup_succeeded = False
        try:
            if entry.session.container_info:
                await self._runtime.stop_container(entry.session.container_info.id)
            cleanup_succeeded = True
        except Exception:
            logger.exception(
                "Failed to clean up session %s; keeping slot reserved until cleanup succeeds",
                token,
            )
        finally:
            async with self._lock:
                current = self._sessions.get(token)
                if current is not entry:
                    return

                if cleanup_succeeded:
                    self._sessions.pop(token, None)
                    return

                current.close_task = None

    def get(self, token: str) -> Session | None:
        entry = self._sessions.get(token)
        if entry is None:
            return None
        return entry.session

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
            self._runtime.close()
        except Exception:
            logger.exception("Failed to close Docker events client")

        async with self._lock:
            tokens = list(self._sessions.keys())

        logger.info("Shutting down %d session(s)", len(tokens))
        await gather(*(self.close(t) for t in tokens))
