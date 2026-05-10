from asyncio import Event, Task
from enum import StrEnum
from secrets import token_hex
from time import time

from pydantic import BaseModel, ConfigDict, Field


class SessionState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
    CLOSING = "closing"


class ConnectResult(StrEnum):
    OK = "ok"
    UNKNOWN = "unknown"
    BUSY = "busy"


class Session(BaseModel):
    created_at: float = Field(default_factory=time)
    token: str = Field(default_factory=lambda: token_hex(24))
    password: str = Field(default_factory=lambda: token_hex(12), repr=False)
    state: SessionState = SessionState.STARTING
    sandbox_id: str | None = None
    error: str | None = None


class SessionCreateResponse(BaseModel):
    url: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SessionStatusResponse(BaseModel):
    active: int = Field(ge=0)
    available: int = Field(ge=0)
    limit: int = Field(gt=0)
    memory: str


class SessionLifecycleEvent(BaseModel):
    state: SessionState
    error: str | None = None
    password: str | None = None


class SessionEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Session
    ready_event: Event = Field(default_factory=Event)
    close_event: Event = Field(default_factory=Event)
    client_connected: bool = False
    start_task: Task[None] | None = None
    close_task: Task[None] | None = None
    timeout_task: Task[None] | None = None
    timeout_cancel: Event | None = None
    cleanup_error: Exception | None = None
