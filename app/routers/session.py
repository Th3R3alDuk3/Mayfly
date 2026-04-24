from logging import getLogger

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect

from app.config import get_settings
from app.models.session import SessionCreateResponse, SessionStatusResponse
from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.post(
    path="/sessions",
    status_code=201,
    response_model=SessionCreateResponse,
    operation_id="create_mayfly_session",
    tags=["mayfly", "opencode", "session", "create"],
    description="Starts a session with its own Mayfly web container.",
)
async def create_session(request: Request) -> SessionCreateResponse:

    settings = get_settings()
    manager: SessionManager = request.app.state.manager

    try:
        session = await manager.create()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return SessionCreateResponse(
        url=f"http://{settings.public_host}:{settings.public_port}/view/{session.token}",
    )


@router.delete(
    path="/sessions/{token}",
    status_code=204,
    operation_id="delete_mayfly_session",
    tags=["mayfly", "opencode", "session", "delete"],
    description="Stops the container associated with the given session token.",
)
async def delete_session(token: str, request: Request) -> None:

    manager: SessionManager = request.app.state.manager

    if not manager.get(token):
        raise HTTPException(status_code=404, detail="Session not found")

    await manager.close(token)


@router.get(
    path="/sessions/status",
    response_model=SessionStatusResponse,
    operation_id="get_mayfly_status",
    tags=["mayfly", "opencode", "session", "status"],
    description="Returns the number of open, free, and maximum containers.",
)
async def get_sessions_status(request: Request) -> SessionStatusResponse:

    manager: SessionManager = request.app.state.manager
    manager_status = manager.status()
    return SessionStatusResponse.model_validate(manager_status)


@router.websocket(
    path="/sessions/{token}/lifecycle"
)
async def ws_session_lifecycle(token: str, websocket: WebSocket) -> None:

    manager: SessionManager = websocket.app.state.manager

    await websocket.accept()

    if not await manager.confirm_connected(token):
        await websocket.close(code=4004, reason="unknown token")
        return

    logger.info(f"Lifecycle WS connected: {token}")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        logger.info(f"Lifecycle WS disconnected: {token}")
    except Exception:
        logger.exception(f"Lifecycle WS error: {token}")
    finally:
        logger.info(f"Lifecycle WS closed: {token} — stopping container")
        await manager.close(token)
