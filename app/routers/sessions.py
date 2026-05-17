from asyncio import TaskGroup
from collections.abc import AsyncIterator
from hmac import compare_digest
from logging import getLogger
from pathlib import PurePosixPath

from fastapi import (
    APIRouter,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)

from app.config import SettingsDep
from app.models.session import (
    ConnectResult,
    Session,
    SessionCreateResponse,
    SessionLifecycleEvent,
    SessionState,
    SessionStatusResponse,
)
from app.services.session import SessionManager

logger = getLogger(__name__)
router = APIRouter()


class _SessionClosedByManager(Exception):
    pass


def _lifecycle_event(session: Session, *, include_password: bool) -> dict[str, object]:
    return SessionLifecycleEvent(
        state=session.state,
        error=session.error,
        password=session.password if include_password else None,
    ).model_dump(mode="json")


@router.get(
    path="/sessions/status",
    operation_id="get_mayfly_status",
    tags=["sessions"],
    description="Returns the number of active, available, and maximum containers.",
)
async def get_sessions_status(
    request: Request,
    token: str | None = None,
) -> SessionStatusResponse:
    manager: SessionManager = request.app.state.manager
    return await manager.status(token)


@router.post(
    path="/sessions",
    status_code=201,
    operation_id="create_mayfly_session",
    tags=["sessions"],
    description="Starts a session with its own Mayfly sandbox.",
)
async def create_session(
    request: Request,
    settings: SettingsDep,
) -> SessionCreateResponse:
    manager: SessionManager = request.app.state.manager

    try:
        session = await manager.create()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return SessionCreateResponse(
        url=f"{settings.public_url}/view/{session.token}",
        password=session.password,
    )


@router.post(
    path="/sessions/{token}/upload",
    status_code=204,
    operation_id="upload_to_mayfly_session",
    tags=["sessions"],
    description="Uploads a single file into the sandbox workspace directory.",
)
async def upload_to_session(
    token: str,
    request: Request,
    settings: SettingsDep,
    file: UploadFile = File(...),
    x_mayfly_password: str = Header(default=""),
) -> None:
    manager: SessionManager = request.app.state.manager

    session = manager.get(token)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if not x_mayfly_password or not compare_digest(x_mayfly_password, session.password):
        logger.warning("Invalid session password")
        raise HTTPException(status_code=401, detail="Invalid session password")

    raw_name = file.filename or ""
    name = PurePosixPath(raw_name.replace("\\", "/")).name
    if not name or name in {".", ".."} or "\x00" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    limit = int(settings.mayfly_upload_limit)
    limit_human = settings.mayfly_upload_limit.human_readable()

    async def chunks() -> AsyncIterator[bytes]:
        sent = 0
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                return
            sent += len(chunk)
            if sent > limit:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {limit_human} limit",
                )
            yield chunk

    try:
        await manager.upload(token, name, chunks())
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.delete(
    path="/sessions/{token}",
    status_code=204,
    operation_id="delete_mayfly_session",
    tags=["sessions"],
    description="Stops the Mayfly sandbox associated with the given session token.",
)
async def delete_session(
    token: str,
    request: Request,
) -> None:
    manager: SessionManager = request.app.state.manager

    if manager.get(token) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        await manager.close(token, raise_errors=True)
    except RuntimeError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error


@router.websocket(path="/sessions/{token}/lifecycle")
async def session_lifecycle(
    token: str,
    websocket: WebSocket,
) -> None:
    manager: SessionManager = websocket.app.state.manager

    await websocket.accept()

    result = await manager.confirm_connected(token)
    if result == ConnectResult.UNKNOWN:
        await websocket.close(code=4004, reason="unknown token")
        return
    if result == ConnectResult.BUSY:
        await websocket.close(code=4009, reason="session already open")
        return

    closed_by_manager = False
    arm_disconnect_timeout = True

    try:
        session = manager.get(token)
        if session is None:
            arm_disconnect_timeout = False
            await websocket.close(code=4004, reason="unknown token")
            return

        await websocket.send_json(_lifecycle_event(session, include_password=True))

        if session.state == SessionState.STARTING:
            session = await manager.wait_ready(token)
            if session is None:
                arm_disconnect_timeout = False
                await websocket.close(code=4004, reason="unknown token")
                return
            await websocket.send_json(_lifecycle_event(session, include_password=True))

        if session.state != SessionState.READY:
            arm_disconnect_timeout = False
            try:
                await websocket.close(code=1011, reason=session.error or "session not ready")
            finally:
                await manager.close(token)
            return

        try:
            async with TaskGroup() as tg:
                async def watch_close() -> None:
                    nonlocal closed_by_manager
                    await manager.wait_closing(token)
                    closed_by_manager = True
                    raise _SessionClosedByManager

                async def receive_messages() -> None:
                    while True:
                        await websocket.receive_text()

                tg.create_task(watch_close())
                tg.create_task(receive_messages())
        except* _SessionClosedByManager:
            pass
        except* WebSocketDisconnect:
            pass

        if closed_by_manager:
            await websocket.close(code=1001, reason="session closed")
    except Exception:
        logger.exception(f"Lifecycle WS error: {token}")
    finally:
        if not closed_by_manager and arm_disconnect_timeout:
            await manager.schedule_disconnect_close(token)
