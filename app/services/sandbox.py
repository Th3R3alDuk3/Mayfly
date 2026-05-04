from asyncio import sleep
from collections.abc import AsyncIterator
from contextlib import suppress
from logging import getLogger
from shlex import quote
from typing import Any, Final

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


_HEALTH_TIMEOUT: Final[int] = 60
_HEALTH_POLL: Final[int] = 3

_UPLOAD_EXIT_TIMEOUT: Final[float] = 30.0
_UPLOAD_POLL: Final[float] = 0.2

_USER: Final[str] = "user"
_UID: Final[int] = 1000
_HOME: Final[str] = f"/home/{_USER}"
_PORT: Final[int] = 4096
_NETWORK: Final[str] = "mayfly-net"

_LABEL: Final[str] = "mayfly.managed"
_LABEL_VALUE: Final[str] = "true"


def _sandbox_host(token: str) -> str:
    return f"mayfly-{token}"


def sandbox_base_url(token: str) -> str:
    return f"http://{_sandbox_host(token)}:{_PORT}/"


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
            await container.delete(force=True)
        except DockerError as error:
            if error.status == 404:
                logger.info(f"Sandbox already gone: {sandbox_id}")
                return
            raise RuntimeError(f"Failed to remove sandbox {sandbox_id}: {error}") from error
        logger.info(f"Sandbox removed: {sandbox_id}")

    async def remove_managed_sandboxes(self) -> None:
        try:
            containers = await self._client.containers.list(
                all=True,
                filters={"label": [f"{_LABEL}={_LABEL_VALUE}"]},
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

        target = f"{_HOME}/{self._settings.mayfly_workspace_dir}/{filename}"
        cmd = ["sh", "-c", f"umask 022 && cat > {quote(target)}"]

        try:
            execute = await container.exec(
                cmd=cmd, stdin=True, stdout=False, stderr=True, user=_USER,
            )
            async with execute.start(detach=False) as stream:
                async for chunk in chunks:
                    await stream.write_in(chunk)

            exit_code: int | None = None
            for _ in range(int(_UPLOAD_EXIT_TIMEOUT / _UPLOAD_POLL)):
                info = await execute.inspect()
                if not info.get("Running"):
                    exit_code = info.get("ExitCode")
                    break
                await sleep(_UPLOAD_POLL)
            else:
                raise RuntimeError(
                    f"Upload exec did not finish within {_UPLOAD_EXIT_TIMEOUT}s"
                )
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
            if error.status != 404 or "no such image" not in error.message.lower():
                raise RuntimeError(f"Docker error: {error}") from error

        try:
            await self._client.images.pull(self._settings.mayfly_image)
        except DockerError as error:
            raise RuntimeError(f"Image not found: {self._settings.mayfly_image}") from error
        return await self._client.containers.create(config=config, name=name)

    def _sandbox_container_config(self, token: str, password: str) -> dict[str, Any]:
        s = self._settings
        env = {
            "MAYFLY_PORT": _PORT,
            "MAYFLY_PASSWORD": password,
            "MAYFLY_WORKSPACE_DIR": s.mayfly_workspace_dir,
            "OPENAI_BASE_URL": s.openai_base_url,
            "OPENAI_API_KEY": s.openai_api_key,
            "OPENAI_MODEL": s.openai_model,
            "OPENAI_CONTEXT_TOKENS": s.openai_context_tokens,
            "OPENAI_OUTPUT_TOKENS": s.openai_output_tokens,
            "OPENAI_TIMEOUT": s.openai_timeout,
            "OPENAI_CHUNK_TIMEOUT": s.openai_chunk_timeout,
            "TZ": s.tz,
        }
        return {
            "Image": s.mayfly_image,
            "Hostname": _sandbox_host(token),
            "User": f"{_UID}:{_UID}",
            "Env": [f"{k}={v}" for k, v in env.items()],
            "Labels": {_LABEL: _LABEL_VALUE},
            "HostConfig": {
                "NetworkMode": _NETWORK,
                "Memory": int(s.mayfly_memory),
                "NanoCpus": int(s.mayfly_cpus * 1_000_000_000),
                "Tmpfs": {
                    _HOME: f"size={int(s.mayfly_home_size)},uid={_UID},exec",
                    "/tmp": f"size={int(s.mayfly_tmp_size)},uid={_UID},exec",
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
        url = f"http://{host}:{_PORT}/"
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
