from logging import getLogger
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.websocket(
    path="/session/{token}/lifecycle"
)
async def lifecycle_ws(token: str, websocket: WebSocket) -> None:
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
