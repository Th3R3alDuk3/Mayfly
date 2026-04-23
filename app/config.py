from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    docker_image: str = Field(min_length=1)
    docker_port: int = Field(ge=1, le=65535)
    max_containers: int = Field(gt=0)
    container_memory: str = Field(min_length=1)
    container_cpus: float = Field(gt=0)
    container_tmpfs_size: str = Field(min_length=1)
    container_timeout: float = Field(gt=0)
    openai_base_url: str = Field(min_length=1)
    openai_model: str = Field(min_length=1)
    openai_context_size: int = Field(gt=0)
    openai_output_size: int = Field(gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
