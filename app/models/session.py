from asyncio import Task
from secrets import token_urlsafe
from time import time
from pydantic import BaseModel, ConfigDict, Field

from app.models.docker import ContainerInfo


class Session(BaseModel):
    created_at: float = Field(default_factory=time)
    token: str = Field(default_factory=lambda: token_urlsafe(32))
    container_info: ContainerInfo | None = None


class ManagedSession(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Session
    abandon_task: Task[None] | None = None
    close_task: Task[None] | None = None


class SessionCreateResponse(BaseModel):
    token: str = Field(min_length=1)
    url: str = Field(min_length=1)


class SessionStatusResponse(BaseModel):
    open: int = Field(ge=0)
    free: int = Field(ge=0)
    max: int = Field(gt=0)
