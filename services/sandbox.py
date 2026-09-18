from asyncio import CancelledError, Task, create_task, sleep, timeout
from contextlib import suppress
from dataclasses import dataclass
from logging import getLogger
from socket import socket

from httpx2 import AsyncClient, HTTPError
from microsandbox import (
    ExecEventType,
    ExecHandle,
    Network,
    NetworkPolicy,
    PortBinding,
    Protocol,
    Rule,
    Sandbox,
    SecurityProfile,
    Volume,
)

from config import get_settings

_settings = get_settings()

logger = getLogger(__name__)

GUEST_PORT = 4096
CONFIG_DIR = "/etc/mayfly"

_LABELS = {"mayfly": "sandbox"}
_READY_TIMEOUT_SECONDS = 90.0
_READY_POLL_SECONDS = 0.5


@dataclass
class Machine:
    sandbox: Sandbox
    port: int
    service: Task[None]


async def boot(
    name: str,
    password: str,
) -> Machine:

    with socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]

    rules: list[Rule] = []

    for entry in _settings.sandbox_allow.split(","):
        if not (entry := entry.strip()):
            continue
        destination, _, allowed_port = entry.partition(":")
        rules.append(Rule.allow(
            destination=destination,
            protocol=Protocol.TCP,
            port=int(allowed_port) if allowed_port else 443,
        ))

    if rules:
        rules = [*Rule.allow_dns(), *rules]

    try:
        sandbox = await Sandbox.create(
            name,
            image=_settings.sandbox_image,
            memory=_settings.sandbox_memory,
            cpus=_settings.sandbox_cpus,
            security=SecurityProfile.RESTRICTED,
            max_duration=_settings.sandbox_max_duration,
            ephemeral=True,
            labels=_LABELS,
            env={"OPENCHAMBER_UI_PASSWORD": password},
            volumes={CONFIG_DIR: Volume.bind(_settings.sandbox_config_dir, readonly=True)},
            ports=[PortBinding.tcp(port, GUEST_PORT)],
            network=Network(policy=NetworkPolicy(rules=tuple(rules))),
        )
    except Exception:
        # A failed start leaves a stopped sandbox record behind.
        with suppress(Exception):
            await Sandbox.remove(name)
        raise

    # Create does not run the image command, and unread output queues without bound.
    async def drain(handle: ExecHandle) -> None:
        async for event in handle:
            if event.event_type == ExecEventType.STDERR:
                logger.debug(f"[{name}] {(event.data or b'').decode(errors='replace').rstrip()}")
            elif event.code is not None:
                logger.warning(f"Sandbox {name} service exited with {event.code}")

    service: Task[None] | None = None

    try:
        service = create_task(drain(await sandbox.exec_default_stream()))

        async with (
            timeout(_READY_TIMEOUT_SECONDS),
            AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client,
        ):
            while True:
                with suppress(HTTPError, ValueError):
                    response = await client.get("/health")
                    if response.status_code == 200 and response.json().get("isOpenCodeReady"):
                        break
                await sleep(_READY_POLL_SECONDS)
    except Exception:
        if service is not None:
            service.cancel()
        await sandbox.destroy(force=True)
        raise

    return Machine(sandbox, port, service)


async def destroy(
    machine: Machine,
) -> None:

    machine.service.cancel()
    with suppress(CancelledError):
        await machine.service
    await machine.sandbox.destroy(force=True)


async def remove_leftovers() -> None:

    page = await Sandbox.list_with(labels=_LABELS)

    for handle in page.sandboxes:
        logger.info(f"Removing leftover sandbox {handle.name}")
        with suppress(Exception):
            await handle.destroy(force=True)
