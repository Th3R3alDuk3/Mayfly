from contextlib import suppress
from logging import getLogger

from aiodocker import Docker
from aiodocker.containers import DockerContainer
from aiodocker.exceptions import DockerError
from httpx import AsyncClient, HTTPStatusError, RequestError
from tenacity import AsyncRetrying, RetryError, retry_if_exception_type, stop_after_delay, wait_fixed

from app.config import Settings
from app.models.docker import Container


logger = getLogger(__name__)


_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 3

_MAYFLY_CONTAINER_PORT = 4096
_MAYFLY_NETWORK = "mayfly-net"

_MANAGED_LABEL = "mayfly.managed"
_MANAGED_LABEL_VALUE = "true"


class DockerRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Docker()

    async def start_session_container(self, session_id: str, password: str) -> Container:
        container: DockerContainer | None = None
        try:
            container = await _run_container(self._client, session_id, password, self._settings)
            ip, port = await _inspect_container(container, _MAYFLY_NETWORK, _MAYFLY_CONTAINER_PORT)

            short_id = container.id[:12]
            logger.info(
                f"Container started: {short_id} (mayfly-{session_id} @ {ip}, host:{port}) — waiting for ready"
            )
            await _wait_ready(ip, _MAYFLY_CONTAINER_PORT)
            logger.info(f"Container ready: {short_id} (mayfly-{session_id})")
            return Container(id=container.id, port=port)
        except Exception:
            if container is not None:
                with suppress(DockerError):
                    await container.stop(t=3)
                    await container.delete()
            raise

    async def stop_container(self, container_id: str) -> None:
        try:
            container = await self._client.containers.get(container_id)
        except DockerError as error:
            if error.status == 404:
                logger.info(f"Container already gone: {container_id}")
                return
            raise

        force_delete = False
        try:
            await container.stop(t=5)
        except DockerError as error:
            if error.status == 404:
                logger.info(f"Container already stopped: {container_id}")
                return
            logger.warning(f"Failed to stop container {container_id}, forcing removal: {error}")
            force_delete = True

        try:
            await container.delete(force=force_delete)
        except DockerError as error:
            if error.status == 404:
                logger.info(f"Container already removed: {container_id}")
                return
            raise RuntimeError(f"Failed to remove container {container_id}: {error}") from error

        logger.info(f"Container {'force ' if force_delete else ''}removed: {container_id}")

    async def remove_managed_containers(self) -> None:
        try:
            containers = await self._client.containers.list(
                all=True,
                filters={"label": [f"{_MANAGED_LABEL}={_MANAGED_LABEL_VALUE}"]},
            )
        except DockerError as error:
            logger.warning(f"Failed to list managed containers: {error}")
            return

        for container in containers:
            try:
                await container.delete(force=True)
                logger.info(f"Removed stale managed container: {container.id[:12]}")
            except DockerError as error:
                if error.status == 404:
                    continue
                logger.warning(f"Failed to remove stale container {container.id[:12]}: {error}")

    async def close(self) -> None:
        await self._client.close()


async def _run_container(
    client: Docker,
    session_id: str,
    password: str,
    settings: Settings,
) -> DockerContainer:
    last_port_error: DockerError | None = None

    for host_port in range(settings.mayfly_host_port_start, settings.mayfly_host_port_end + 1):
        try:
            container = await _create_or_pull(client, settings, session_id, password, host_port)
        except DockerError as error:
            raise RuntimeError(f"Docker error: {error}") from error

        try:
            await container.start()
            return container
        except DockerError as error:
            with suppress(DockerError):
                await container.delete(force=True)
            if _is_port_binding_error(error):
                last_port_error = error
                logger.info(f"Mayfly host port {host_port} unavailable, trying next port")
                continue
            raise RuntimeError(f"Docker error: {error}") from error

    raise RuntimeError(
        "No available mayfly host ports in configured range "
        f"{settings.mayfly_host_port_start}-{settings.mayfly_host_port_end}"
    ) from last_port_error


async def _create_or_pull(
    client: Docker,
    settings: Settings,
    session_id: str,
    password: str,
    host_port: int,
) -> DockerContainer:
    config = _session_container_config(session_id, password, settings, host_port)
    name = f"mayfly-{session_id}"
    try:
        return await client.containers.create(config=config, name=name)
    except DockerError as error:
        if error.status == 404 and "no such image" in str(error).lower():
            try:
                await client.images.pull(settings.mayfly_image)
            except DockerError as pull_error:
                raise RuntimeError(f"Image not found: {settings.mayfly_image}") from pull_error
            return await client.containers.create(config=config, name=name)
        raise


def _session_container_config(
    session_id: str,
    password: str,
    settings: Settings,
    host_port: int,
) -> dict[str, object]:
    env = {
        "MAYFLY_PORT": _MAYFLY_CONTAINER_PORT,
        "MAYFLY_PASSWORD": password,
        "OPENAI_BASE_URL": settings.openai_base_url,
        "OPENAI_MODEL": settings.openai_model,
        "OPENAI_CONTEXT_TOKENS": settings.openai_context_tokens,
        "OPENAI_OUTPUT_TOKENS": settings.openai_output_tokens,
        "OPENAI_TIMEOUT": settings.openai_timeout,
        "OPENAI_CHUNK_TIMEOUT": settings.openai_chunk_timeout,
        "TZ": settings.tz,
    }
    return {
        "Image": settings.mayfly_image,
        "Hostname": f"mayfly-{session_id}",
        "User": "1000:1000",
        "ExposedPorts": {f"{_MAYFLY_CONTAINER_PORT}/tcp": {}},
        "Env": [f"{key}={value}" for key, value in env.items()],
        "Labels": {_MANAGED_LABEL: _MANAGED_LABEL_VALUE},
        "HostConfig": {
            "NetworkMode": _MAYFLY_NETWORK,
            "PortBindings": {
                f"{_MAYFLY_CONTAINER_PORT}/tcp": [
                    {"HostIp": settings.mayfly_bind_host, "HostPort": str(host_port)}
                ],
            },
            "Memory": _parse_bytes(settings.mayfly_memory),
            "NanoCpus": int(settings.mayfly_cpus * 1_000_000_000),
            "Tmpfs": {
                "/home/user": f"size={settings.mayfly_tmpfs_size},uid=1000,exec",
                "/tmp": "size=64m,uid=1000,exec",
            },
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "PidsLimit": 256,
            "ExtraHosts": ["host.docker.internal:host-gateway"],
            "AutoRemove": False,
        },
        "NetworkingConfig": {
            "EndpointsConfig": {_MAYFLY_NETWORK: {}},
        },
    }


def _parse_bytes(value: str) -> int:
    text = value.strip().lower()
    suffixes = {"k": 1024, "m": 1024**2, "g": 1024**3}
    if text and text[-1] in suffixes:
        return int(float(text[:-1]) * suffixes[text[-1]])
    return int(text)


def _is_port_binding_error(error: DockerError) -> bool:
    message = str(error).lower()
    return (
        "port is already allocated" in message
        or "bind: address already in use" in message
        or ("listen tcp" in message and "address already in use" in message)
    )


async def _inspect_container(
    container: DockerContainer,
    network: str,
    container_port: int,
) -> tuple[str, int]:
    info = await container.show()
    network_settings = info.get("NetworkSettings", {})

    ip = network_settings.get("Networks", {}).get(network, {}).get("IPAddress")
    if not ip:
        raise RuntimeError(f"Container {container.id[:12]}: no IP on {network}")

    bindings = network_settings.get("Ports", {}).get(f"{container_port}/tcp") or []
    if not bindings:
        raise RuntimeError(f"Container {container.id[:12]}: no host port mapped for {container_port}/tcp")

    return ip, int(bindings[0]["HostPort"])


async def _wait_ready(host: str, port: int) -> None:
    url = f"http://{host}:{port}/"
    async with AsyncClient(timeout=3) as client:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_delay(_HEALTH_TIMEOUT),
                wait=wait_fixed(_HEALTH_POLL),
                retry=retry_if_exception_type((RequestError, HTTPStatusError)),
            ):
                with attempt:
                    response = await client.get(url)
                    response.raise_for_status()
        except RetryError as error:
            raise TimeoutError(f"Mayfly not ready at {url} after {_HEALTH_TIMEOUT}s") from error
