from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    public_host: str = Field(min_length=1)
    app_port: int = Field(ge=1, le=65535)
    #
    tz: str = Field(min_length=1)
    #
    mayfly_image: str = Field(min_length=1)
    mayfly_host_port_start: int = Field(default=40000, ge=1, le=65535)
    mayfly_host_port_end: int = Field(default=40100, ge=1, le=65535)
    mayfly_bind_host: str = Field(min_length=1)
    mayfly_max_sessions: int = Field(gt=0)
    mayfly_memory: str = Field(min_length=1)
    mayfly_cpus: float = Field(gt=0)
    mayfly_tmpfs_size: str = Field(min_length=1)
    mayfly_connect_timeout: float = Field(gt=0)
    mayfly_disconnect_timeout: float = Field(gt=0)
    #
    openai_base_url: str = Field(min_length=1)
    openai_model: str = Field(min_length=1)
    openai_context_tokens: int = Field(gt=0)
    openai_output_tokens: int = Field(gt=0)
    openai_timeout: int = Field(gt=0)
    openai_chunk_timeout: int = Field(gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
