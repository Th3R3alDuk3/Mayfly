from asyncio import FIRST_COMPLETED, CancelledError, create_task, wait
from contextlib import suppress
from logging import getLogger

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.config import SettingsDep
from app.models.session import (
    ConnectResult,
    SessionCreateResponse,
    SessionState,
    SessionStatusResponse,
)
from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.get(
    path="/sessions/status",
    response_model=SessionStatusResponse,
    operation_id="get_mayfly_status",
    tags=["sessions"],
    description="Returns the number of active, available, and maximum containers.",
)
async def get_sessions_status(
    request: Request,
) -> SessionStatusResponse:

    manager: SessionManager = request.app.state.manager
    manager_status = manager.status()
    return SessionStatusResponse.model_validate(manager_status)


@router.post(
    path="/sessions",
    status_code=201,
    response_model=SessionCreateResponse,
    operation_id="create_mayfly_session",
    tags=["sessions"],
    description="Starts a session with its own Mayfly web container.",
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
        url=f"https://{settings.public_domain}:{settings.public_port}/view/{session.token}",
    )


@router.delete(
    path="/sessions/{token}",
    status_code=204,
    operation_id="delete_mayfly_session",
    tags=["sessions"],
    description="Stops the container associated with the given session token.",
)
async def delete_session(
    token: str,
    request: Request,
) -> None:

    manager: SessionManager = request.app.state.manager

    if not manager.get(token):
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

    logger.info(f"Lifecycle WS connected: {token}")

    session = manager.get(token)
    if session is None:
        await websocket.close(code=4004, reason="unknown token")
        return

    await websocket.send_json({"state": session.state, "error": session.error})

    if session.state == SessionState.STARTING:
        session = await manager.wait_ready(token)
        if session is None:
            await websocket.close(code=4004, reason="unknown token")
            return
        await websocket.send_json({"state": session.state, "error": session.error})

    if session.state != SessionState.READY:
        await websocket.close(code=1011, reason=session.error or "session not ready")
        await manager.close(token)
        return

    closed_by_manager = False
    receive_task = create_task(websocket.receive_text())
    closing_task = create_task(manager.wait_closing(token))

    try:
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
            if not task.done():
                task.cancel()
                with suppress(CancelledError):
                    await task
            else:
                with suppress(CancelledError, Exception):
                    task.result()

        if closed_by_manager:
            logger.info(f"Lifecycle WS closed by manager: {token}")
        else:
            logger.info(f"Lifecycle WS closed: {token} — arming grace timeout")
            await manager.schedule_idle_close(token)
