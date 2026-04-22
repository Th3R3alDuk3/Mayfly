from secrets import token_urlsafe
from time import time
from pydantic import BaseModel, Field

from app.models.docker import ContainerInfo


class Session(BaseModel):
    created_at: float = Field(default_factory=time)
    token: str = Field(default_factory=lambda: token_urlsafe(32))
    container_info: ContainerInfo | None = None
