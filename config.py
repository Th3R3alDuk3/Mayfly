from functools import cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # base URL clients reach this server at; used in session links
    public_url: str

    max_concurrent_sandboxes: int

    sandbox_image: str
    # MiB, whole vCPUs
    sandbox_memory: int
    sandbox_cpus: int
    # seconds; a session with no open browser connection is destroyed after this
    sandbox_idle_timeout: float
    # seconds; hard lifetime of each microVM
    sandbox_max_duration: float
    # holds opencode.json; mounted read-only at /etc/mayfly
    sandbox_config_dir: str
    # comma-separated `host:PORT` for this machine, `DOMAIN[:PORT]` or `*.DOMAIN[:PORT]`; empty = offline
    sandbox_allow: str
    # comma-separated file extensions the browser may upload; empty = any
    sandbox_upload_extensions: str
    # bytes; largest file the browser may upload
    sandbox_upload_max_bytes: int


@cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]
