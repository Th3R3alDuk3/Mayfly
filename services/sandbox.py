from asyncio import CancelledError, Task, create_task, sleep, timeout
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from functools import cache
from socket import socket
from urllib.parse import urlsplit

from fastmcp.utilities.logging import get_logger
from httpx2 import AsyncClient, HTTPError
from microsandbox import (
    Destination,
    ExecEventType,
    ExecHandle,
    Network,
    NetworkPolicy,
    PortBinding,
    Rule,
    Sandbox,
    SecurityProfile,
    Volume,
)
from microsandbox.types import DnsConfig

from config import get_settings

_settings = get_settings()
logger = get_logger(__name__)

_SANDBOX_PORT = 4096
# mounted read-only into every sandbox
_SANDBOX_DIR = "./sandbox"
_SANDBOX_MOUNT = "/etc/mayfly"

_LABELS = {"mayfly": "sandbox"}
_READY_TIMEOUT_SECONDS = 90.0
_READY_POLL_SECONDS = 0.5


@cache
def _network() -> Network:

    specs = _settings.sandbox_allow.replace(",", " ").split()

    # The indexes and PyPI's file host.
    for url in (_settings.sandbox_index_url, _settings.sandbox_npm_registry):
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        specs.append(f"{parts.hostname}:{port}")
    specs.append("files.pythonhosted.org")

    rules: list[Rule] = []

    for spec in specs:
        destination, _, port = spec.partition(":")
        if destination.startswith("*."):
            destination = Destination.domain_suffix(destination[2:])
        # A domain rule for the alias does not reach the host.
        elif destination == "host.microsandbox.internal":
            destination = "host"
        rules.append(Rule.allow(
            destination=destination,
            port=int(port) if port else 443,
        ))

    return Network(
        # Ingress stays open for the published port.
        policy=NetworkPolicy(rules=tuple(rules)),
        # Allows a LAN index or model endpoint.
        dns=DnsConfig(rebind_protection=False),
    )


@asynccontextmanager
async def boot_sandbox(
    name: str,
    password: str,
) -> AsyncGenerator[tuple[int, Task[None]]]:

    with socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]

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
            env={
                "OPENCHAMBER_UI_PASSWORD": password,
                "OPENCHAMBER_FS_UPLOAD_MAX_BYTES": str(
                    _settings.sandbox_upload_max_bytes),
                "PIP_INDEX_URL": _settings.sandbox_index_url,
                "PIP_TRUSTED_HOST": _settings.sandbox_insecure_host,
                "UV_DEFAULT_INDEX": _settings.sandbox_index_url,
                "UV_INSECURE_HOST": _settings.sandbox_insecure_host,
                "NPM_CONFIG_REGISTRY": _settings.sandbox_npm_registry,
                "NPM_CONFIG_STRICT_SSL":
                    "false" if _settings.sandbox_insecure_host else "true",
            },
            volumes={_SANDBOX_MOUNT: Volume.bind(_SANDBOX_DIR, readonly=True)},
            ports=[PortBinding.tcp(port, _SANDBOX_PORT)],
            network=_network(),
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
                logger.debug("sandbox %s: %s", name,
                    (event.data or b"").decode(errors="replace").rstrip())
            elif event.code is not None:
                logger.warning("sandbox %s: service exited with %d", name, event.code)

    # Exiting destroys the sandbox, also after its hard lifetime already removed it.
    async with sandbox:

        service = create_task(drain(await sandbox.exec_default_stream()))

        try:
            async with (
                timeout(_READY_TIMEOUT_SECONDS),
                AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=5) as client,
            ):
                while not service.done():
                    with suppress(HTTPError, ValueError):
                        response = await client.get("/health")
                        if response.status_code == 200 \
                            and response.json().get("isOpenCodeReady"):
                            break
                    await sleep(_READY_POLL_SECONDS)
                else:
                    raise RuntimeError("service exited before it was ready")

            yield port, service
        finally:
            service.cancel()
            with suppress(CancelledError):
                await service


async def remove_leftovers() -> None:

    page = await Sandbox.list_with(labels=_LABELS)

    for handle in page.sandboxes:
        logger.info("removing leftover sandbox %s", handle.name)
        with suppress(Exception):
            await handle.destroy(force=True)
