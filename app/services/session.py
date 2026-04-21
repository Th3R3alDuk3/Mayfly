import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field

from app.config import Settings
from app.services.docker import ContainerInfo, start_container, stop_container

log = logging.getLogger(__name__)


@dataclass
class Session:
    token: str
    container_id: str
    host_port: int
    created_at: float = field(default_factory=time.time)


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> Session:
        async with self._lock:
            if len(self._sessions) >= self._settings.max_containers:
                raise RuntimeError("Max container limit reached")
            token = uuid.uuid4().hex
            self._sessions[token] = Session(token=token, container_id="", host_port=0)

        try:
            info: ContainerInfo = await start_container(token, self._settings)
        except Exception:
            async with self._lock:
                self._sessions.pop(token, None)
            raise

        session = Session(token=token, container_id=info.container_id, host_port=info.host_port)
        async with self._lock:
            self._sessions[token] = session
        return session

    async def close(self, token: str) -> None:
        async with self._lock:
            session = self._sessions.pop(token, None)
        if session and session.container_id:
            await stop_container(session.container_id)

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
        await asyncio.gather(*(self.close(t) for t in tokens))
