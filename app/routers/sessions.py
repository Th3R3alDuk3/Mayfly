from asyncio import FIRST_COMPLETED, CancelledError, Task, create_task, wait
from contextlib import suppress
from logging import getLogger

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

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


def _lifecycle_event(
    session: Session,
    host: str,
    *,
    include_password: bool = False,
) -> SessionLifecycleEvent:
    url = (
        f"http://{_url_host(host)}:{session.container.port}/"
        if session.state == SessionState.READY and session.container is not None
        else None
    )
    password = session.password if include_password else None
    return SessionLifecycleEvent(state=session.state, error=session.error, url=url, password=password)


def _client_host(candidate: str | None, fallback: str) -> str:
    host = (candidate or "").strip()
    return host or fallback


def _url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


@router.get(
    path="/sessions/status",
    operation_id="get_mayfly_status",
    tags=["sessions"],
    description="Returns the number of active, available, and maximum containers.",
)
async def get_sessions_status(
    request: Request,
) -> SessionStatusResponse:

    manager: SessionManager = request.app.state.manager
    return manager.status()


@router.post(
    path="/sessions",
    status_code=201,
    operation_id="create_mayfly_session",
    tags=["sessions"],
    description="Starts a session with its own Mayfly sandbox.",
)
async def create_session(
    request: Request,
) -> SessionCreateResponse:

    manager: SessionManager = request.app.state.manager

    try:
        session = await manager.create()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return SessionCreateResponse(
        url=str(request.url_for("view_session", token=session.token)),
        password=session.password,
    )


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


@router.websocket(
    path="/sessions/{token}/lifecycle"
)
async def session_lifecycle(
    token: str,
    websocket: WebSocket,
    settings: SettingsDep,
) -> None:

    manager: SessionManager = websocket.app.state.manager
    host = _client_host(websocket.url.hostname, settings.public_host)

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
    receive_task: Task[str] | None = None
    closing_task: Task[None] | None = None

    try:
        logger.info(f"Lifecycle WS connected: {token}")

        session = manager.get(token)
        if session is None:
            arm_disconnect_timeout = False
            await websocket.close(code=4004, reason="unknown token")
            return

        await websocket.send_json(
            _lifecycle_event(session, host, include_password=True).model_dump(mode="json")
        )

        if session.state == SessionState.STARTING:
            session = await manager.wait_ready(token)
            if session is None:
                arm_disconnect_timeout = False
                await websocket.close(code=4004, reason="unknown token")
                return
            await websocket.send_json(
                _lifecycle_event(session, host, include_password=True).model_dump(mode="json")
            )

        if session.state != SessionState.READY:
            arm_disconnect_timeout = False
            try:
                await websocket.close(code=1011, reason=session.error or "session not ready")
            finally:
                await manager.close(token)
            return

        receive_task = create_task(websocket.receive_text())
        closing_task = create_task(manager.wait_closing(token))

        while True:
            done, _ = await wait(
                {receive_task, closing_task},
                return_when=FIRST_COMPLETED,
            )

            if receive_task in done:
                receive_task.result()
                receive_task = create_task(websocket.receive_text())

            if closing_task in done:
                closed_by_manager = True
                await websocket.close(code=1001, reason="session closed")
                return
    except WebSocketDisconnect:
        logger.info(f"Lifecycle WS disconnected: {token}")
    except Exception:
        logger.exception(f"Lifecycle WS error: {token}")
    finally:
        for task in (receive_task, closing_task):
            if task is None:
                continue
            if not task.done():
                task.cancel()
                with suppress(CancelledError):
                    await task
            else:
                with suppress(CancelledError, Exception):
                    task.result()

        if closed_by_manager:
            logger.info(f"Lifecycle WS closed by manager: {token}")
        elif arm_disconnect_timeout:
            logger.info(f"Lifecycle WS closed: {token} — starting disconnect timeout")
            await manager.schedule_disconnect_close(token)
