from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    opencode_image: str = "opencode-plane:latest"
    max_containers: int = 5
    container_memory: str = "3g"
    container_cpus: float = 1.0
    container_tmpfs_size: str = "2g"
    opencode_port: int = 4096
    public_host: str = "localhost"
    ollama_base_url: str = "http://host.docker.internal:11434/v1"
    ollama_model: str = "gemma4:e4b"


@lru_cache
def get_settings() -> Settings:
    return Settings()
