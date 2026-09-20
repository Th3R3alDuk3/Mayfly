from asyncio import CancelledError, Semaphore, Task, create_task, shield, sleep
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from secrets import token_hex
from time import monotonic

from fastmcp.utilities.logging import get_logger

from config import get_settings
from services.sandbox import boot_sandbox, remove_leftovers

_settings = get_settings()
logger = get_logger(__name__)


# Its message is safe to show to the caller.
class SessionError(Exception):
    pass


@dataclass
class Session:
    token: str
    password: str
    port: int
    # Runs the image command; done once OpenChamber or the sandbox is gone.
    service: Task[None]
    # Holds the slot and the sandbox until closed.
    stack: AsyncExitStack
    # Proxied requests and WebSockets currently open.
    connections: int = 0
    idle_since: float = field(default_factory=monotonic)


_sessions: dict[str, Session] = {}
_slots = Semaphore(_settings.max_concurrent_sandboxes)


async def open_session() -> Session:

    # The acquire fast path never suspends, so this cannot race the acquire.
    if _slots.locked():
        raise SessionError("Server at capacity. Try again later.")

    token, password = token_hex(24), token_hex(12)

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(_slots)

        try:
            port, service = await stack.enter_async_context(
                boot_sandbox(f"mayfly-{token[:12]}", password))
        except Exception as error:
            logger.exception("session %s: start failed", token[:12])
            raise SessionError(
                "Could not start the sandbox. Try again later.") from error

        session = _sessions[token] = Session(
            token, password, port, service, stack.pop_all())

    logger.info("session %s: opened on port %d", token[:12], port)
    return session


def get_session(
    token: str | None,
) -> Session | None:

    return _sessions.get(token) if token else None


def session_url(
    token: str,
) -> str:

    return f"{_settings.public_url.rstrip('/')}/s/{token}"


def connect_session(
    session: Session,
) -> None:

    session.connections += 1


def disconnect_session(
    session: Session,
) -> None:

    session.connections -= 1
    if session.connections == 0:
        session.idle_since = monotonic()


async def close_session(
    token: str,
) -> None:

    session = _sessions.pop(token, None)

    if session is not None:
        # A cancelled shutdown must not cut the teardown short.
        try:
            await shield(session.stack.aclose())
        except Exception:
            logger.exception("session %s: teardown failed", token[:12])
        else:
            logger.info("session %s: closed", token[:12])


async def _reap_sessions() -> None:

    while True:
        await sleep(1)
        now = monotonic()
        for token, session in list(_sessions.items()):
            idle = session.connections == 0 \
                and now - session.idle_since > _settings.sandbox_idle_timeout
            if idle or session.service.done():
                logger.info("session %s: %s", token[:12], "idle" if idle else "died")
                await close_session(token)


@asynccontextmanager
async def session_lifespan() -> AsyncGenerator[None]:

    await remove_leftovers()

    reaper = create_task(_reap_sessions())

    try:
        yield
    finally:

        reaper.cancel()

        # Ctrl-C cancels this once; the remaining sessions still close.
        for token in list(_sessions):
            with suppress(CancelledError):
                await close_session(token)
