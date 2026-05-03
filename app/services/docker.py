from collections.abc import AsyncIterator
from contextlib import suppress
from logging import getLogger
from shlex import quote
from typing import Any

from aiodocker import Docker
from aiodocker.containers import DockerContainer
from aiodocker.exceptions import DockerError
from httpx import AsyncClient, HTTPStatusError, RequestError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_delay,
    wait_fixed,
)

from app.config import Settings
from app.models.docker import Container

logger = getLogger(__name__)


_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 3

_MAYFLY_USER = "user"
_MAYFLY_UID = 1000
_MAYFLY_HOME = f"/home/{_MAYFLY_USER}"
_MAYFLY_CONTAINER_PORT = 4096
_MAYFLY_NETWORK = "mayfly-net"

_MANAGED_LABEL = "mayfly.managed"
_MANAGED_LABEL_VALUE = "true"


def _is_port_binding_error(error: DockerError) -> bool:
    if error.status != 500:
        return False
    message = error.message.lower()
    return "address already in use" in message or "port is already allocated" in message


class DockerRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Docker()

    async def start_session_container(self, token: str, password: str) -> Container:
        container: DockerContainer | None = None
        try:
            container = await self._run_container(token, password)
            ip, port = await self._inspect_container(container)

            short_id = container.id[:12]
            logger.info(
                f"Container started: {short_id} "
                f"(mayfly-{token} @ {ip}, host:{port}) — waiting for ready"
            )
            await self._wait_ready(ip, _MAYFLY_CONTAINER_PORT)
            logger.info(f"Container ready: {short_id} (mayfly-{token})")
            return Container(id=container.id, port=port)
        except Exception:
            if container is not None:
                with suppress(DockerError):
                    await container.stop(t=3)
                    await container.delete()
            raise

    async def upload_to_workspace(
        self,
        container_id: str,
        filename: str,
        chunks: AsyncIterator[bytes],
    ) -> None:
        try:
            container = await self._client.containers.get(container_id)
        except DockerError as error:
            if error.status == 404:
                raise RuntimeError("Container not found") from error
            raise

        target_path = f"{_MAYFLY_HOME}/{self._settings.mayfly_workspace_dir}/{filename}"
        cmd = ["sh", "-c", f"umask 022 && cat > {quote(target_path)}"]

        try:
            execute = await container.exec(
                cmd=cmd,
                stdin=True,
                stdout=False,
                stderr=True,
                user=_MAYFLY_USER,
            )
            async with execute.start(detach=False) as stream:
                async for chunk in chunks:
                    await stream.write_in(chunk)

            exit_code = (await execute.inspect()).get("ExitCode")
        except DockerError as error:
            raise RuntimeError(f"Upload failed: {error}") from error

        if exit_code is None:
            raise RuntimeError("Upload exec returned no exit code")
        if exit_code != 0:
            raise RuntimeError(f"Upload exec failed (exit {exit_code})")

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

    async def _run_container(self, token: str, password: str) -> DockerContainer:
        last_port_error: DockerError | None = None
        settings = self._settings

        for host_port in range(settings.mayfly_host_port_start, settings.mayfly_host_port_end + 1):
            try:
                container = await self._create_or_pull(token, password, host_port)
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
        self,
        token: str,
        password: str,
        host_port: int,
    ) -> DockerContainer:
        config = self._session_container_config(token, password, host_port)
        name = f"mayfly-{token}"
        try:
            return await self._client.containers.create(config=config, name=name)
        except DockerError as error:
            if error.status == 404 and "no such image" in error.message.lower():
                try:
                    await self._client.images.pull(self._settings.mayfly_image)
                except DockerError as pull_error:
                    raise RuntimeError(
                        f"Image not found: {self._settings.mayfly_image}"
                    ) from pull_error
                return await self._client.containers.create(config=config, name=name)
            raise

    def _session_container_config(
        self,
        token: str,
        password: str,
        host_port: int,
    ) -> dict[str, Any]:
        settings = self._settings
        env = {
            "MAYFLY_PORT": _MAYFLY_CONTAINER_PORT,
            "MAYFLY_PASSWORD": password,
            "MAYFLY_WORKSPACE_DIR": settings.mayfly_workspace_dir,
            "OPENAI_BASE_URL": settings.openai_base_url,
            "OPENAI_API_KEY": settings.openai_api_key,
            "OPENAI_MODEL": settings.openai_model,
            "OPENAI_CONTEXT_TOKENS": settings.openai_context_tokens,
            "OPENAI_OUTPUT_TOKENS": settings.openai_output_tokens,
            "OPENAI_TIMEOUT": settings.openai_timeout,
            "OPENAI_CHUNK_TIMEOUT": settings.openai_chunk_timeout,
            "TZ": settings.tz,
        }
        return {
            "Image": settings.mayfly_image,
            "Hostname": f"mayfly-{token}",
            "User": f"{_MAYFLY_UID}:{_MAYFLY_UID}",
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
                "Memory": int(settings.mayfly_memory),
                "NanoCpus": int(settings.mayfly_cpus * 1_000_000_000),
                "Tmpfs": {
                    _MAYFLY_HOME: (
                        f"size={int(settings.mayfly_home_size)},uid={_MAYFLY_UID},exec"
                    ),
                    "/tmp": f"size={int(settings.mayfly_tmp_size)},uid={_MAYFLY_UID},exec",
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

    async def _inspect_container(self, container: DockerContainer) -> tuple[str, int]:
        info = await container.show()
        network_settings = info.get("NetworkSettings", {})

        ip = network_settings.get("Networks", {}).get(_MAYFLY_NETWORK, {}).get("IPAddress")
        if not ip:
            raise RuntimeError(f"Container {container.id[:12]}: no IP on {_MAYFLY_NETWORK}")

        bindings = network_settings.get("Ports", {}).get(f"{_MAYFLY_CONTAINER_PORT}/tcp") or []
        if not bindings:
            raise RuntimeError(
                f"Container {container.id[:12]}: "
                f"no host port mapped for {_MAYFLY_CONTAINER_PORT}/tcp"
            )

        return ip, int(bindings[0]["HostPort"])

    async def _wait_ready(self, host: str, port: int) -> None:
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
                raise TimeoutError(
                    f"Mayfly not ready at {url} after {_HEALTH_TIMEOUT}s"
                ) from error
