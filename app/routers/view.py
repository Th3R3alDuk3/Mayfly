from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

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


@router.post(
    path="/view",
    include_in_schema=False,
)
async def create_view_session(
    request: Request,
    background_tasks: BackgroundTasks,
) -> RedirectResponse:

    manager: SessionManager = request.app.state.manager

    try:
        session = await manager.reserve()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    background_tasks.add_task(manager.start, session.token)
    return RedirectResponse(
        url=f"/view/{session.token}",
        status_code=303,
        background=background_tasks,
    )


@router.get(
    path="/view/{token}",
    include_in_schema=False,
    response_class=HTMLResponse,
)
async def view_session(
    token: str,
    request: Request,
    settings: SettingsDep,
) -> HTMLResponse:

    manager: SessionManager = request.app.state.manager

    session = manager.get(token)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    container_url = f"https://{token}.{settings.public_domain}:{settings.public_port}/"
    return templates.TemplateResponse(
        request=request,
        name="view.html",
        context={
            "container_url": container_url,
            "token": token,
        },
    )


async def http_exception_handler(
    request: Request, 
    exception: StarletteHTTPException,
) -> Response:

    if "text/html" in request.headers.get("accept", ""):
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": exception.status_code, "detail": exception.detail},
            status_code=exception.status_code,
        )

    return JSONResponse(
        status_code=exception.status_code,
        content={"detail": exception.detail},
    )
