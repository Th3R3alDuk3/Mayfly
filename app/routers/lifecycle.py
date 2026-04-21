from logging import getLogger
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.websocket("/session/{token}/lifecycle")
async def lifecycle_ws(token: str, websocket: WebSocket) -> None:
    manager: SessionManager = websocket.app.state.manager

    if not manager.get(token):
        await websocket.close(code=4004)
        return

    await websocket.accept()
    logger.info("Lifecycle WS connected: %s", token[:8])

    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        logger.error("Error")
    finally:
        logger.info("Lifecycle WS closed: %s — stopping container", token[:8])
        await manager.close(token)
