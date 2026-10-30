from asyncio import CancelledError, Task, create_task, sleep, timeout
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from functools import cache
from socket import socket
from urllib.parse import urlsplit

from fastmcp.utilities.logging import get_logger
from httpx2 import AsyncClient, HTTPError, Timeout
from microsandbox import (
    Action,
    Destination,
    Direction,
    ExecEventType,
    ExecHandle,
    HttpConfig,
    Network,
    NetworkPolicy,
    OutboundProxy,
    PortBinding,
    Protocol,
    Rule,
    Sandbox,
    ScopedVerifyUpstream,
    SecurityProfile,
    TlsConfig,
    Volume,
)
from microsandbox.types import DnsConfig

from config import get_settings

_settings = get_settings()
logger = get_logger(__name__)

_SANDBOX_PORT = 4096
# Mounted read-only into every sandbox; OpenCode reads it as its system config.
_SANDBOX_DIR = "./sandbox"
_SANDBOX_MOUNT = "/etc/opencode"

_LABELS = {"mayfly": "sandbox"}
_READY_TIMEOUT_SECONDS = 90.0
_READY_POLL_SECONDS = 0.5


@cache
def _network() -> Network:

    entries = [
        *_settings.sandbox_allow.replace(",", " ").split(),
        _settings.sandbox_index_url,
        _settings.sandbox_npm_registry,
        "files.pythonhosted.org",
    ]

    rules: list[Rule] = []
    # A port is either intercepted or plain, so the scheme decides per entry.
    tls_ports = {443}

    for entry in entries:
        url = urlsplit(entry if "://" in entry else f"https://{entry}")
        host = url.hostname or ""
        port = url.port or (443 if url.scheme == "https" else 80)

        if url.scheme == "https":
            tls_ports.add(port)

        if host.startswith("*."):
            destination = Destination.domain_suffix(host[2:])
        # A domain rule for the alias does not reach the host.
        elif host == "host.microsandbox.internal":
            destination = "host"
        else:
            destination = host

        rules.append(Rule.allow(destination=destination, port=port))

    return Network(
        policy=NetworkPolicy(default_ingress=Action.DENY, rules=(
            *rules,
            Rule.allow(
                direction=Direction.INGRESS,
                protocol=Protocol.TCP,
                port=_SANDBOX_PORT,
            ),
        )),
        # Allows a LAN index or model endpoint.
        dns=DnsConfig(rebind_protection=False),
        # Rules by name pass HTTPS only when it is intercepted.
        tls=TlsConfig(
            intercepted_ports=tuple(sorted(tls_ports)),
            # QUIC would pass by name without interception.
            block_quic=True,
            scoped_verify_upstream=(
                (ScopedVerifyUpstream(_settings.sandbox_insecure_host, False),)
                if _settings.sandbox_insecure_host else ()
            ),
        ),
        # Unknown names already fail at DNS; this covers other ports and IP literals.
        http=HttpConfig(network_deny_message=(
            f"Blocked by the sandbox network policy; allowed: {', '.join(entries)}.")),
    )


@asynccontextmanager
async def boot_sandbox(
    name: str,
    password: str,
) -> AsyncGenerator[tuple[Task[None], AsyncClient]]:

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
                "NPM_CONFIG_REGISTRY": _settings.sandbox_npm_registry,
            },
            volumes={_SANDBOX_MOUNT: Volume.bind(_SANDBOX_DIR, readonly=True)},
            ports=[PortBinding.tcp(port, _SANDBOX_PORT)],
            network=_network(),
            proxy=(  # pyright: ignore[reportCallIssue]
                OutboundProxy.http_connect(_settings.sandbox_proxy)
                if _settings.sandbox_proxy else None
            ),
        )
    except Exception:
        # A failed start can leave a stopped or even running sandbox behind.
        with suppress(Exception):
            await (await Sandbox.get(name)).destroy(force=True)
        raise

    # Create does not run the image command, and unread output queues without bound.
    async def drain(handle: ExecHandle) -> None:
        async for event in handle:
            if event.event_type == ExecEventType.STDERR:
                logger.info("sandbox %s: %s", name,
                    (event.data or b"").decode(errors="replace").rstrip())
            elif event.code is not None:
                logger.warning("sandbox %s: service exited with %d", name, event.code)

    # Exiting destroys the sandbox, also after its hard lifetime already removed it.
    async with sandbox, AsyncClient(
        base_url=f"http://127.0.0.1:{port}",
        timeout=Timeout(None, connect=5),
        # A proxy for the sandbox's traffic must not catch this loopback.
        trust_env=False,
    ) as client:

        # Intercepted HTTPS cannot fall back once a proxy refuses an IPv6 target.
        if _settings.sandbox_proxy:
            await sandbox.exec(
                "sh",
                ["-c", "echo 1 > /proc/sys/net/ipv6/conf/all/disable_ipv6"],
                user="root",
            )

        service = create_task(drain(await sandbox.exec_default_stream()))

        try:
            async with timeout(_READY_TIMEOUT_SECONDS):
                while not service.done():
                    with suppress(HTTPError, ValueError):
                        response = await client.get("/health", timeout=5)
                        if response.status_code == 200 \
                            and response.json().get("isOpenCodeReady"):
                            break
                    await sleep(_READY_POLL_SECONDS)
                else:
                    raise RuntimeError("service exited before it was ready")

            yield service, client
        finally:
            service.cancel()
            with suppress(CancelledError):
                await service


async def remove_leftovers() -> None:

    page = await Sandbox.list_with(labels=_LABELS, limit=100)

    for handle in page.sandboxes:
        logger.info("removing leftover sandbox %s", handle.name)
        with suppress(Exception):
            await handle.destroy(force=True)
