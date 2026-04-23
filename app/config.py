from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore",
    )

    docker_image: str
    docker_port: int
    #
    max_containers: int
    container_memory: str
    container_cpus: float
    container_tmpfs_size: str
    abandon_timeout_seconds: float
    #
    openai_base_url: str
    openai_model: str
    openai_context_size: int
    openai_output_size: int


@lru_cache
def get_settings() -> Settings:
    return Settings()
