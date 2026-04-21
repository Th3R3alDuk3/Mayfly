import asyncio
import logging
import time
import urllib.request
from dataclasses import dataclass

from docker.errors import APIError, ImageNotFound, NotFound

import docker
from app.config import Settings

log = logging.getLogger(__name__)

_HEALTH_TIMEOUT = 60
_HEALTH_POLL = 2


@dataclass
class ContainerInfo:
    container_id: str
    host_port: int


def _wait_ready(host_port: int) -> None:
    url = f"http://localhost:{host_port}/"
    deadline = time.monotonic() + _HEALTH_TIMEOUT
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310
            return
        except Exception:
            time.sleep(_HEALTH_POLL)
    raise TimeoutError(f"OpenCode not ready on port {host_port} after {_HEALTH_TIMEOUT}s")


async def start_container(session_id: str, settings: Settings) -> ContainerInfo:
    def _start() -> ContainerInfo:
        client = docker.from_env()
        env: dict[str, str] = {
            "OLLAMA_BASE_URL": settings.ollama_base_url,
            "OLLAMA_MODEL": settings.ollama_model,
            "OPENCODE_PORT": str(settings.opencode_port),
        }

        try:
            container = client.containers.run(
                settings.opencode_image,
                detach=True,
                name=f"opencode-{session_id}",
                ports={f"{settings.opencode_port}/tcp": None},
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
                environment=env,
                extra_hosts={"host.docker.internal": "host-gateway"},
                auto_remove=False,
            )
        except ImageNotFound as e:
            raise RuntimeError(f"Image not found: {settings.opencode_image}") from e
        except APIError as e:
            raise RuntimeError(f"Docker error: {e}") from e
        container.reload()
        port_bindings = container.ports.get(f"{settings.opencode_port}/tcp") or []
        if not port_bindings:
            container.stop(timeout=3)
            container.remove()
            raise RuntimeError(f"Container {session_id}: no port binding found")
        host_port = int(port_bindings[0]["HostPort"])
        log.info("Container started: %s on port %d — waiting for ready", container.short_id, host_port)
        _wait_ready(host_port)
        log.info("Container ready: %s on port %d", container.short_id, host_port)
        return ContainerInfo(container_id=container.id, host_port=host_port)

    return await asyncio.to_thread(_start)


async def stop_container(container_id: str) -> None:
    def _stop() -> None:
        try:
            client = docker.from_env()
            c = client.containers.get(container_id)
            c.stop(timeout=5)
            c.remove()
            log.info("Container removed: %s", container_id[:12])
        except NotFound:
            pass
        except Exception:
            log.exception("Error stopping container %s", container_id[:12])

    await asyncio.to_thread(_stop)
