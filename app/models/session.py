from asyncio import Event, Task
from enum import StrEnum
from secrets import token_hex, token_urlsafe
from time import time

from pydantic import BaseModel, ConfigDict, Field

from app.models.docker import Container


class SessionState(StrEnum):
    STARTING = "starting"
    READY = "ready"
    ERROR = "error"
    CLOSING = "closing"


def _session_token() -> str:
    return token_hex(24)


def _ui_password() -> str:
    return token_urlsafe(18)


class Session(BaseModel):
    created_at: float = Field(default_factory=time)
    token: str = Field(default_factory=_session_token)
    password: str = Field(default_factory=_ui_password, repr=False)
    state: SessionState = SessionState.STARTING
    container: Container | None = None
    error: str | None = None


class SessionCreateResponse(BaseModel):
    url: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SessionStatusResponse(BaseModel):
    active: int = Field(ge=0)
    available: int = Field(ge=0)
    limit: int = Field(gt=0)


class SessionLifecycleEvent(BaseModel):
    state: SessionState
    error: str | None = None
    url: str | None = None
    password: str | None = None


class ConnectResult(StrEnum):
    OK = "ok"
    UNKNOWN = "unknown"
    BUSY = "busy"


class SessionEntry(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    session: Session
    ready_event: Event = Field(default_factory=Event)
    close_event: Event = Field(default_factory=Event)
    client_connected: bool = False
    start_task: Task[None] | None = None
    close_task: Task[None] | None = None
    cleanup_task: Task[None] | None = None
    cleanup_error: Exception | None = None
