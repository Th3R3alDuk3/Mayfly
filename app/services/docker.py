from asyncio import to_thread
from http.client import HTTPResponse
from logging import getLogger
from time import monotonic, sleep
from urllib.request import urlopen
from docker import from_env
from docker.errors import APIError, ImageNotFound, NotFound

from app.config import Settings
from app.models.docker import ContainerInfo


logger = getLogger(__name__)


_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 3


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
        try:
            container = client.containers.run(
                settings.docker_image,
                detach=True,
                name=f"opencode-{session_id}",
                hostname="mayfly",
                ports={f"{settings.docker_port}/tcp": None},
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
            )
        except ImageNotFound as e:
            raise RuntimeError(f"Image not found: {settings.docker_image}") from e
        except APIError as e:
            raise RuntimeError(f"Docker error: {e}") from e
        try:
            container.reload()
            port_bindings = container.ports.get(f"{settings.docker_port}/tcp") or []
            if not port_bindings:
                raise RuntimeError(f"Container {session_id}: no port binding found")
            host_port = int(port_bindings[0]["HostPort"])
            logger.info("Container started: %s on port %d — waiting for ready", container.short_id, host_port)
            _wait_ready(host_port)
            logger.info("Container ready: %s on port %d", container.short_id, host_port)
            return ContainerInfo(id=container.id, port=host_port)
        except Exception:
            try:
                container.stop(timeout=3)
                container.remove()
            except Exception:
                logger.exception("Cleanup failed for container %s", container.short_id)
            raise

    return await to_thread(_start)


async def stop_container(container_id: str) -> None:

    def _stop() -> None:
        client = from_env()
        try:
            try:
                container = client.containers.get(container_id)
            except NotFound:
                logger.info("Container already gone: %s", container_id)
                return

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

            logger.info("Container removed: %s", container_id)
        except NotFound:
            logger.info("Container already gone: %s", container_id)
        finally:
            client.close()

    await to_thread(_stop)
