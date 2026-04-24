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
    url: str = Field(
        min_length=1,
        description="URL for web container access."
    )


class SessionStatusResponse(BaseModel):
    open: int = Field(
        ge=0, 
        description="Currently running containers.",
    )
    free: int = Field(
        ge=0, 
        description="Additional containers that can be started.",
    )
    max: int = Field(
        gt=0, 
        description="Maximum number of concurrent containers.",
    )
