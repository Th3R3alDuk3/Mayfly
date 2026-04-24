from asyncio import (
    CancelledError,
    Lock,
    Task,
    create_task,
    gather,
    sleep,
)
from logging import getLogger

from app.config import Settings
from app.models.session import ManagedSession, Session
from app.services.docker import DockerRuntime


logger = getLogger(__name__)


def _cancel_task(task: Task[None] | None) -> None:
    if task:
        task.cancel()


class SessionManager:
    def __init__(self, settings: Settings, runtime: DockerRuntime | None = None) -> None:
        self._settings = settings
        self._runtime = runtime or DockerRuntime(settings)
        self._sessions: dict[str, ManagedSession] = {}
        self._lock = Lock()

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
        _cancel_task(entry.abandon_task)
        entry.abandon_task = None

        try:
            if entry.session.container_info:
                await self._runtime.stop_container(entry.session.container_info.id)
        except Exception:
            logger.exception("Failed to clean up session %s", token)
        finally:
            async with self._lock:
                self._sessions.pop(token, None)

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
        async with self._lock:
            tokens = list(self._sessions.keys())

        logger.info("Shutting down %d session(s)", len(tokens))
        await gather(*(self.close(t) for t in tokens))
