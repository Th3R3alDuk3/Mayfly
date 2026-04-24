from logging import getLogger
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings
from app.models.session import SessionCreateResponse, SessionStatusResponse
from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


_VIEW_HTML = Path("app/static/view.html").read_text(encoding="utf-8")


@router.post(
    path="/session",
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
        url=f"http://{settings.public_host}:{settings.public_port}/" \
            f"{session.container_info.port}/{session.token}",
    )


@router.get(
    path="/status",
    response_model=SessionStatusResponse,
    operation_id="get_mayfly_status",
    tags=["mayfly", "opencode", "session", "status"],
    description="Returns the number of open, free, and maximum containers.",
)
async def get_status(request: Request) -> SessionStatusResponse:

    manager: SessionManager = request.app.state.manager
    manager_status = manager.status()
    return SessionStatusResponse.model_validate(manager_status)


@router.get(
    path="/{port:int}/{token}",
    include_in_schema=False,
    response_class=HTMLResponse,
)
async def view_session(port: int, token: str, request: Request) -> HTMLResponse:

    settings = get_settings()
    manager: SessionManager = request.app.state.manager

    if not manager.get(token):
        raise HTTPException(status_code=404, detail="Session not found")

    container_url = f"http://{settings.public_host}:{port}/"
    html = _VIEW_HTML.replace("{{container_url}}", container_url).replace("{{token}}", token)
    return HTMLResponse(html)
