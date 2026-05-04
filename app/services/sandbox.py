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

logger = getLogger(__name__)


_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 3

_MAYFLY_USER = "user"
_MAYFLY_UID = 1000
_MAYFLY_HOME = f"/home/{_MAYFLY_USER}"
_MAYFLY_PORT = 4096
_MAYFLY_NETWORK = "mayfly-net"

_MANAGED_LABEL = "mayfly.managed"
_MANAGED_LABEL_VALUE = "true"


def _sandbox_host(token: str) -> str:
    return f"mayfly-{token}"


def sandbox_base_url(token: str) -> str:
    return f"http://{_sandbox_host(token)}:{_MAYFLY_PORT}/"


class SandboxRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = Docker()

    async def close(self) -> None:
        await self._client.close()

    async def start_sandbox(self, token: str, password: str) -> str:
        host = _sandbox_host(token)
        container: DockerContainer | None = None
        try:
            container = await self._create_or_pull(token, password)
            await container.start()

            short_id = container.id[:12]
            logger.info(f"Sandbox started: {short_id} ({host}) — waiting for ready")
            await self._wait_ready(host)
            logger.info(f"Sandbox ready: {short_id} ({host})")
            return container.id
        except Exception:
            if container is not None:
                with suppress(DockerError):
                    await container.delete(force=True)
            raise

    async def stop_sandbox(self, sandbox_id: str) -> None:
        try:
            container = await self._client.containers.get(sandbox_id)
        except DockerError as error:
            if error.status == 404:
                logger.info(f"Sandbox already gone: {sandbox_id}")
                return
            raise

        try:
            await container.delete(force=True)
        except DockerError as error:
            if error.status == 404:
                logger.info(f"Sandbox already removed: {sandbox_id}")
                return
            raise RuntimeError(f"Failed to remove sandbox {sandbox_id}: {error}") from error

        logger.info(f"Sandbox removed: {sandbox_id}")

    async def remove_managed_sandboxes(self) -> None:
        try:
            containers = await self._client.containers.list(
                all=True,
                filters={"label": [f"{_MANAGED_LABEL}={_MANAGED_LABEL_VALUE}"]},
            )
        except DockerError as error:
            logger.warning(f"Failed to list managed sandboxes: {error}")
            return

        for container in containers:
            try:
                await container.delete(force=True)
                logger.info(f"Removed stale managed sandbox: {container.id[:12]}")
            except DockerError as error:
                if error.status == 404:
                    continue
                logger.warning(f"Failed to remove stale sandbox {container.id[:12]}: {error}")

    async def upload_to_workspace(
        self,
        sandbox_id: str,
        filename: str,
        chunks: AsyncIterator[bytes],
    ) -> None:
        try:
            container = await self._client.containers.get(sandbox_id)
        except DockerError as error:
            if error.status == 404:
                raise RuntimeError("Sandbox not found") from error
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

    async def _create_or_pull(self, token: str, password: str) -> DockerContainer:
        config = self._sandbox_container_config(token, password)
        name = _sandbox_host(token)
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
            raise RuntimeError(f"Docker error: {error}") from error

    def _sandbox_container_config(self, token: str, password: str) -> dict[str, Any]:
        settings = self._settings
        env = {
            "MAYFLY_PORT": _MAYFLY_PORT,
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
            "Hostname": _sandbox_host(token),
            "User": f"{_MAYFLY_UID}:{_MAYFLY_UID}",
            "Env": [f"{key}={value}" for key, value in env.items()],
            "Labels": {_MANAGED_LABEL: _MANAGED_LABEL_VALUE},
            "HostConfig": {
                "NetworkMode": _MAYFLY_NETWORK,
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
        }

    async def _wait_ready(self, host: str) -> None:
        url = f"http://{host}:{_MAYFLY_PORT}/"
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
