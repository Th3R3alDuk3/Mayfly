from logging import getLogger
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.post("/session", status_code=201)
async def create_session(request: Request) -> JSONResponse:
    manager: SessionManager = request.app.state.manager
    settings = get_settings()

    try:
        session = await manager.create()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    url = f"http://{settings.public_host}:{session.host_port}/"
    return JSONResponse({
        "token": session.token, 
        "url": url,
    }, status_code=201)


@router.get("/status")
async def get_status(request: Request) -> dict[str, int]:
    manager: SessionManager = request.app.state.manager
    return manager.status()
