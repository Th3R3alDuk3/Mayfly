from asyncio import to_thread
from http.client import HTTPResponse
from logging import getLogger
from time import monotonic, sleep
from urllib.request import urlopen

from docker import from_env
from docker.client import DockerClient
from docker.errors import APIError, ImageNotFound, NotFound
from docker.models.containers import Container

from app.config import Settings
from app.models.docker import ContainerInfo


logger = getLogger(__name__)

_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 3
_CONTAINER_HOSTNAME = "mayfly"


def _container_name(session_id: str) -> str:
    return f"opencode-{session_id}"


def _container_tmpfs(settings: Settings) -> dict[str, str]:
    return {
        "/home/user": f"size={settings.container_tmpfs_size},uid=1000,exec",
        "/tmp": "size=64m,uid=1000,exec",
    }


def _container_environment(settings: Settings) -> dict[str, str | int]:
    return {
        "DOCKER_PORT": settings.docker_port,
        "OPENAI_BASE_URL": settings.openai_base_url,
        "OPENAI_MODEL": settings.openai_model,
        "OPENAI_CONTEXT_SIZE": settings.openai_context_size,
        "OPENAI_OUTPUT_SIZE": settings.openai_output_size,
    }


def _run_container(client: DockerClient, session_id: str, settings: Settings) -> Container:
    try:
        return client.containers.run(
            settings.docker_image,
            detach=True,
            name=_container_name(session_id),
            hostname=_CONTAINER_HOSTNAME,
            ports={f"{settings.docker_port}/tcp": None},
            mem_limit=settings.container_memory,
            nano_cpus=int(settings.container_cpus * 1_000_000_000),
            tmpfs=_container_tmpfs(settings),
            read_only=True,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            pids_limit=256,
            user="1000:1000",
            environment=_container_environment(settings),
            extra_hosts={"host.docker.internal": "host-gateway"},
            auto_remove=False,
        )
    except ImageNotFound as e:
        raise RuntimeError(f"Image not found: {settings.docker_image}") from e
    except APIError as e:
        raise RuntimeError(f"Docker error: {e}") from e


def _host_port(container: Container, docker_port: int, session_id: str) -> int:
    container.reload()
    port_bindings = container.ports.get(f"{docker_port}/tcp") or []
    if not port_bindings:
        raise RuntimeError(f"Container {session_id}: no port binding found")
    return int(port_bindings[0]["HostPort"])


def _cleanup_failed_start(container: Container) -> None:
    try:
        container.stop(timeout=3)
        container.remove()
    except Exception:
        logger.exception("Cleanup failed for container %s", container.short_id)


def _get_container(client: DockerClient, container_id: str) -> Container | None:
    try:
        return client.containers.get(container_id)
    except NotFound:
        logger.info("Container already gone: %s", container_id)
        return None


def _stop_and_remove_container(container: Container, container_id: str) -> None:
    try:
        container.stop(timeout=5)
    except NotFound:
        logger.info("Container already stopped: %s", container_id)
        return
    except APIError as e:
        raise RuntimeError(f"Failed to stop container {container_id}: {e}") from e

    try:
        container.remove()
    except NotFound:
        logger.info("Container already removed: %s", container_id)
        return
    except APIError as e:
        raise RuntimeError(f"Failed to remove container {container_id}: {e}") from e


def _wait_ready(host_port: int) -> HTTPResponse:
    url = f"http://localhost:{host_port}/"
    deadline = monotonic() + _HEALTH_TIMEOUT
    while monotonic() < deadline:
        try:
            return urlopen(url, timeout=3)
        except Exception:
            sleep(_HEALTH_POLL)
    raise TimeoutError(f"OpenCode not ready on port {host_port} after {_HEALTH_TIMEOUT}s")


async def start_container(session_id: str, settings: Settings) -> ContainerInfo:
    def _start() -> ContainerInfo:
        client = from_env()
        container: Container | None = None

        try:
            container = _run_container(client, session_id, settings)
            host_port = _host_port(container, settings.docker_port, session_id)
            logger.info(
                "Container started: %s on port %d — waiting for ready",
                container.short_id,
                host_port,
            )
            _wait_ready(host_port)
            logger.info("Container ready: %s on port %d", container.short_id, host_port)
            return ContainerInfo(id=container.id, port=host_port)
        except Exception:
            if container is not None:
                _cleanup_failed_start(container)
            raise
        finally:
            client.close()

    return await to_thread(_start)


async def stop_container(container_id: str) -> None:
    def _stop() -> None:
        client = from_env()
        try:
            container = _get_container(client, container_id)
            if container is None:
                return

            _stop_and_remove_container(container, container_id)
            logger.info("Container removed: %s", container_id)
        finally:
            client.close()

    await to_thread(_stop)
