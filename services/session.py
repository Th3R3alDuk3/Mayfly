from asyncio import Semaphore, gather, sleep
from dataclasses import dataclass, field
from logging import getLogger
from secrets import token_hex
from time import monotonic

from config import get_settings
from services.sandbox import Machine, boot, destroy

_settings = get_settings()

logger = getLogger(__name__)

_REAP_INTERVAL_SECONDS = 5.0


@dataclass
class Entry:
    machine: Machine
    password: str
    connections: int = 0
    idle_since: float = field(default_factory=monotonic)


_entries: dict[str, Entry] = {}
_slots = Semaphore(_settings.max_concurrent_sandboxes)


async def create() -> tuple[str, str]:

    # The acquire fast path never suspends, so this cannot race the acquire.
    if _slots.locked():
        raise RuntimeError("Server at capacity. Try again later.")

    await _slots.acquire()
    token, password = token_hex(24), token_hex(12)

    try:
        machine = await boot(f"mayfly-{token[:12]}", password)
    except Exception:
        _slots.release()
        raise

    _entries[token] = Entry(machine, password)
    logger.info(f"Session {token[:12]} ready on port {machine.port}")

    return token, password


def get(
    token: str | None,
) -> Entry | None:

    return _entries.get(token) if token else None


def url(
    token: str,
) -> str:

    return f"{_settings.public_url.rstrip('/')}/s/{token}"


def connect(
    entry: Entry,
) -> None:

    entry.connections += 1


def disconnect(
    entry: Entry,
) -> None:

    entry.connections -= 1
    if entry.connections == 0:
        entry.idle_since = monotonic()


async def close(
    token: str,
) -> None:

    entry = _entries.pop(token, None)
    if entry is None:
        return

    try:
        await destroy(entry.machine)
    finally:
        _slots.release()

    logger.info(f"Session {token[:12]} closed")


async def close_all() -> None:

    await gather(*(close(token) for token in list(_entries)))


async def reap() -> None:

    while True:
        await sleep(_REAP_INTERVAL_SECONDS)
        now = monotonic()

        for token, entry in list(_entries.items()):
            idle = entry.connections == 0 and now - entry.idle_since > _settings.sandbox_idle_timeout
            if idle or entry.machine.service.done():
                logger.info(f"Session {token[:12]} {'idle' if idle else 'died'} — closing")
                await close(token)
