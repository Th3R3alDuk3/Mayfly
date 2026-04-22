from logging import getLogger
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.post(
    path="/session",
    status_code=201,
    tags=["mayfly", "opencode", "session", "create"],
    description="Startet eine Session mit eigenem OpenCode-Web-Container.",
)
async def create_session(request: Request) -> JSONResponse:
    manager: SessionManager = request.app.state.manager
    settings = get_settings()

    try:
        session = await manager.create()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    assert session.container_info is not None
    url = f"http://{settings.public_host}:{session.container_info.port}/"
    return JSONResponse({
        "token": session.token,
        "url": url,
    }, status_code=201)


@router.get(
    path="/status",
    tags=["mayfly", "opencode", "session", "status"],
    description="Liefert Belegung: offene, freie und maximale Container.",
)
async def get_status(request: Request) -> dict[str, int]:
    manager: SessionManager = request.app.state.manager
    return manager.status()
