from asyncio import to_thread
from logging import getLogger
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen

from docker import from_env
from docker.client import DockerClient
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container as DockerContainer

from app.config import Settings
from app.models.docker import Container


logger = getLogger(__name__)


_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 3

_MANAGED_LABEL = "mayfly.managed"
_MANAGED_LABEL_VALUE = "true"


class DockerRuntime:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: DockerClient = from_env()

    async def start_session_container(self, session_id: str) -> Container:
        return await to_thread(self._start_session_container, session_id)

    async def stop_container(self, container_id: str) -> None:
        await to_thread(self._stop_container, container_id)

    async def remove_managed_containers(self) -> None:
        await to_thread(self._remove_managed_containers)

    def close(self) -> None:
        self._client.close()

    def _start_session_container(self, session_id: str) -> Container:
        container: DockerContainer | None = None
        try:
            container = _run_container(self._client, session_id, self._settings)
            name = f"mayfly-{session_id}"
            ip = _container_ip(container, self._settings.docker_network)

            logger.info(f"Container started: {container.short_id} ({name} @ {ip}) — waiting for ready")
            _wait_ready(ip, self._settings.docker_port)
            logger.info(f"Container ready: {container.short_id} ({name})")
            return Container(id=container.id, name=name)
        except Exception:
            if container is not None:
                try:
                    container.stop(timeout=3)
                    container.remove()
                except Exception:
                    logger.exception(f"Cleanup failed for container {container.short_id}")
            raise

    def _stop_container(self, container_id: str) -> None:
        try:
            container = self._client.containers.get(container_id)
        except NotFound:
            logger.info(f"Container already gone: {container_id}")
            return

        try:
            container.stop(timeout=5)
        except NotFound:
            logger.info(f"Container already stopped: {container_id}")
            return
        except APIError as error:
            logger.warning(f"Failed to stop container {container_id}, forcing removal: {error}")
            try:
                container.remove(force=True)
            except NotFound:
                logger.info(f"Container already removed: {container_id}")
                return
            except APIError as remove_error:
                raise RuntimeError(
                    f"Failed to stop or force-remove container {container_id}: {remove_error}"
                ) from remove_error
            logger.info(f"Container force removed: {container_id}")
            return

        try:
            container.remove()
        except NotFound:
            logger.info(f"Container already removed: {container_id}")
            return
        except APIError as error:
            raise RuntimeError(f"Failed to remove container {container_id}: {error}") from error

        logger.info(f"Container removed: {container_id}")

    def _remove_managed_containers(self) -> None:
        try:
            containers = self._client.containers.list(
                all=True,
                filters={"label": f"{_MANAGED_LABEL}={_MANAGED_LABEL_VALUE}"},
            )
        except APIError as error:
            logger.warning(f"Failed to list managed containers: {error}")
            return

        for container in containers:
            try:
                container.remove(force=True)
                logger.info(f"Removed stale managed container: {container.short_id} ({container.name})")
            except NotFound:
                continue
            except APIError as error:
                logger.warning(f"Failed to remove stale container {container.short_id}: {error}")


def _run_container(client: DockerClient, session_id: str, settings: Settings) -> DockerContainer:
    try:
        return client.containers.run(
            settings.docker_image,
            detach=True,
            name=f"mayfly-{session_id}",
            hostname="mayfly",
            network=settings.docker_network,
            mem_limit=settings.container_memory,
            nano_cpus=int(settings.container_cpus * 1_000_000_000),
            tmpfs={
                "/home/user": f"size={settings.container_tmpfs_size},uid=1000,exec",
                "/tmp": "size=64m,uid=1000,exec",
            },
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=256,
            user="1000:1000",
            environment={
                "DOCKER_PORT": settings.docker_port,
                "OPENAI_BASE_URL": settings.openai_base_url,
                "OPENAI_MODEL": settings.openai_model,
                "OPENAI_CONTEXT_SIZE": settings.openai_context_size,
                "OPENAI_OUTPUT_SIZE": settings.openai_output_size,
            },
            extra_hosts={"host.docker.internal": "host-gateway"},
            auto_remove=False,
            labels={_MANAGED_LABEL: _MANAGED_LABEL_VALUE},
        )
    except ImageNotFound as error:
        raise RuntimeError(f"Image not found: {settings.docker_image}") from error
    except APIError as error:
        raise RuntimeError(f"Docker error: {error}") from error


def _container_ip(container: DockerContainer, network: str) -> str:
    container.reload()
    networks = container.attrs.get("NetworkSettings", {}).get("Networks", {})
    ip = networks.get(network, {}).get("IPAddress")
    if not ip:
        raise RuntimeError(f"Container {container.short_id}: no IP on {network}")
    return ip


def _wait_ready(host: str, port: int) -> None:
    url = f"http://{host}:{port}/"
    deadline = monotonic() + _HEALTH_TIMEOUT
    while monotonic() < deadline:
        try:
            urlopen(url, timeout=3).close()
            return
        except (URLError, TimeoutError, ConnectionError, OSError):
            sleep(_HEALTH_POLL)
    raise TimeoutError(f"Mayfly not ready at {url} after {_HEALTH_TIMEOUT}s")
