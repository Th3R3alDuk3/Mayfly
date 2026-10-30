from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # base URL browsers reach this server at; OpenChamber checks origins against it
    public_url: str

    max_concurrent_sandboxes: int

    sandbox_image: str
    # where pip/uv and npm inside a session install from; reachable by themselves
    sandbox_index_url: str
    sandbox_npm_registry: str
    # empty unless an index uses an own certificate or plain HTTP
    sandbox_insecure_host: str
    # IP:port for http and https traffic; empty for none
    sandbox_proxy: str = ""
    # MiB, whole vCPUs
    sandbox_memory: int
    sandbox_cpus: int
    # seconds; a session with no open browser connection is destroyed after this
    sandbox_idle_timeout: float
    # seconds; hard lifetime of each microVM
    sandbox_max_duration: float
    # comma-separated DOMAIN, *.DOMAIN or URLs, besides the indexes
    sandbox_allow: str
    # comma-separated file extensions the browser may upload; empty = any
    sandbox_upload_extensions: str
    # bytes; largest file the browser may upload
    sandbox_upload_max_bytes: int


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
