from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import SettingsDep
from app.services.session import SessionManager


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    path="/",
    include_in_schema=False,
    response_class=HTMLResponse,
)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="index.html",
    )


@router.get(
    path="/view/{token}",
    include_in_schema=False,
    response_class=HTMLResponse,
)
async def view_session(token: str, request: Request, settings: SettingsDep) -> HTMLResponse:

    manager: SessionManager = request.app.state.manager

    session = manager.get(token)
    if session is None or session.container_info is None:
        raise HTTPException(status_code=404, detail="Session not found")

    container_url = f"http://{settings.public_host}:{session.container_info.port}/"
    return templates.TemplateResponse(
        request=request,
        name="view.html",
        context={"container_url": container_url, "token": token},
    )
