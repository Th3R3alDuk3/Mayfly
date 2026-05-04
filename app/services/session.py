from asyncio import Event, Lock, create_task, gather, timeout
from collections.abc import AsyncIterator
from contextlib import suppress
from logging import getLogger
from typing import Literal

from app.config import Settings
from app.models.session import (
    ConnectResult,
    Session,
    SessionEntry,
    SessionState,
    SessionStatusResponse,
)
from app.services.sandbox import SandboxRuntime

logger = getLogger(__name__)


class SessionManager:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._runtime = SandboxRuntime(settings)
        self._sessions: dict[str, SessionEntry] = {}
        self._lock = Lock()

    async def remove_managed_sandboxes(self) -> None:
        await self._runtime.remove_managed_sandboxes()

    def get(self, token: str) -> Session | None:
        entry = self._sessions.get(token)
        if entry is None:
            return None
        return entry.session

    def status(self) -> SessionStatusResponse:
        active = len(self._sessions)
        return SessionStatusResponse(
            active=active,
            available=max(0, self._settings.mayfly_max_sessions - active),
            limit=self._settings.mayfly_max_sessions,
        )

    async def reserve(self, *, arm_connect_timeout: bool = True) -> Session:
        entry = SessionEntry(session=Session())
        token = entry.session.token

        async with self._lock:
            if len(self._sessions) >= self._settings.mayfly_max_sessions:
                raise RuntimeError("Max mayfly limit reached")
            self._sessions[token] = entry
            if arm_connect_timeout:
                self._arm_timeout(entry, token, self._settings.mayfly_connect_timeout, "connect")

        return entry.session

    async def create(self) -> Session:
        session = await self.reserve(arm_connect_timeout=False)
        try:
            await self.start(session.token, remove_on_error=True)
        except Exception as error:
            raise RuntimeError(str(error)) from error

        async with self._lock:
            entry = self._sessions.get(session.token)
            if entry is None or entry.session.state != SessionState.READY:
                raise RuntimeError("Session was closed during start")
            if entry.timeout_task is None and not entry.client_connected:
                self._arm_timeout(
                    entry, session.token, self._settings.mayfly_connect_timeout, "connect"
                )

        return session

    async def start(self, token: str, remove_on_error: bool = False) -> None:
        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None or entry.session.state != SessionState.STARTING:
                return
            if entry.start_task is None:
                entry.start_task = create_task(
                    self._start_sandbox(token, entry, remove_on_error)
                )
            start_task = entry.start_task

        await start_task

    async def wait_ready(self, token: str) -> Session | None:
        entry = self._sessions.get(token)
        if entry is None:
            return None
        await entry.ready_event.wait()
        return entry.session

    async def wait_closing(self, token: str) -> None:
        entry = self._sessions.get(token)
        if entry is None:
            return
        await entry.close_event.wait()

    async def confirm_connected(self, token: str) -> ConnectResult:
        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None or entry.session.state == SessionState.CLOSING:
                return ConnectResult.UNKNOWN
            if entry.client_connected:
                return ConnectResult.BUSY

            entry.client_connected = True
            cancel = entry.timeout_cancel
            entry.timeout_cancel = None
            entry.timeout_task = None

        if cancel is not None:
            cancel.set()
        return ConnectResult.OK

    async def schedule_disconnect_close(self, token: str) -> None:
        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None or entry.session.state == SessionState.CLOSING:
                return
            entry.client_connected = False
            if entry.timeout_task is not None:
                return
            self._arm_timeout(
                entry, token, self._settings.mayfly_disconnect_timeout, "disconnect"
            )

    async def upload(self, token: str, filename: str, chunks: AsyncIterator[bytes]) -> None:
        entry = self._sessions.get(token)
        if entry is None:
            raise RuntimeError("Session not found")
        if entry.session.state != SessionState.READY or entry.session.sandbox is None:
            raise RuntimeError("Session not ready")
        await self._runtime.upload_to_workspace(entry.session.sandbox.id, filename, chunks)

    async def close(self, token: str, *, raise_errors: bool = False) -> None:
        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return

            if entry.close_task is None:
                entry.cleanup_error = None
                entry.client_connected = False
                entry.session.state = SessionState.CLOSING
                entry.ready_event.set()
                entry.close_event.set()
                entry.close_task = create_task(self._close_session(token, entry))

            close_task = entry.close_task

        await close_task
        if raise_errors and entry.cleanup_error is not None:
            raise RuntimeError(f"Failed to clean up session {token}") from entry.cleanup_error

    async def close_all(self) -> None:
        async with self._lock:
            tokens = list(self._sessions.keys())

        logger.info(f"Shutting down {len(tokens)} session(s)")
        try:
            await gather(*(self.close(t) for t in tokens))
        finally:
            await self._runtime.close()

    def _arm_timeout(
        self,
        entry: SessionEntry,
        token: str,
        delay: float,
        timeout_name: Literal["connect", "disconnect"],
    ) -> None:
        cancel = Event()
        entry.timeout_cancel = cancel
        entry.timeout_task = create_task(
            self._close_after_timeout(token, delay, timeout_name, cancel)
        )

    async def _start_sandbox(
        self,
        token: str,
        entry: SessionEntry,
        remove_on_error: bool,
    ) -> None:
        try:
            sandbox = await self._runtime.start_sandbox(token, entry.session.password)
        except Exception as error:
            logger.exception(f"Failed to start session {token}")
            cancel: Event | None = None
            async with self._lock:
                current = self._sessions.get(token)
                if current is not None and current is entry:
                    current.session.state = SessionState.ERROR
                    current.session.error = str(error)
                    current.start_task = None
                    current.ready_event.set()
                    if remove_on_error:
                        cancel = current.timeout_cancel
                        current.timeout_cancel = None
                        current.timeout_task = None
                        self._sessions.pop(token, None)
            if cancel is not None:
                cancel.set()
            raise

        stop_sandbox_id: str | None = None
        async with self._lock:
            current = self._sessions.get(token)
            if (
                current is None
                or current is not entry
                or current.session.state == SessionState.CLOSING
            ):
                stop_sandbox_id = sandbox.id
            else:
                current.session.state = SessionState.READY
                current.session.sandbox = sandbox
                current.start_task = None
                current.ready_event.set()

        if stop_sandbox_id is not None:
            await self._runtime.stop_sandbox(stop_sandbox_id)

    async def _close_after_timeout(
        self,
        token: str,
        delay: float,
        timeout_name: Literal["connect", "disconnect"],
        cancel: Event,
    ) -> None:
        try:
            async with timeout(delay):
                await cancel.wait()
            return
        except TimeoutError:
            pass

        async with self._lock:
            entry = self._sessions.get(token)
            if entry is None:
                return
            entry.timeout_task = None
            entry.timeout_cancel = None
            if entry.client_connected:
                return

        logger.warning(f"Session {token} reached {timeout_name} timeout after {delay}s — closing")
        await self.close(token)

    async def _close_session(self, token: str, entry: SessionEntry) -> None:
        cancel = entry.timeout_cancel
        entry.timeout_cancel = None
        entry.timeout_task = None
        if cancel is not None:
            cancel.set()

        cleanup_succeeded = False
        try:
            if entry.start_task is not None:
                with suppress(Exception):
                    await entry.start_task
            if entry.session.sandbox is not None:
                await self._runtime.stop_sandbox(entry.session.sandbox.id)
            cleanup_succeeded = True
        except Exception as error:
            entry.cleanup_error = error
            logger.exception(f"Failed to clean up session {token}")

        async with self._lock:
            if cleanup_succeeded:
                self._sessions.pop(token, None)
            else:
                entry.close_task = None
