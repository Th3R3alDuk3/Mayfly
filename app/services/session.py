from asyncio import CancelledError, Lock, Task, create_task, gather, sleep
import logging

from app.config import Settings
from app.models.docker import ContainerInfo
from app.models.session import Session
from app.services.docker import start_container, stop_container

log = logging.getLogger(__name__)


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._abandon_tasks: dict[str, Task[None]] = {}
        self._lock = Lock()

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
        tokens = list(self._sessions.keys())
        log.info("Shutting down %d session(s)", len(tokens))
        await gather(*(self.close(t) for t in tokens))
