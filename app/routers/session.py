from logging import getLogger
from fastapi import APIRouter, HTTPException, Request

from app.models.session import SessionCreateResponse, SessionStatusResponse
from app.services.session import SessionManager


logger = getLogger(__name__)
router = APIRouter()


@router.post(
    path="/session",
    status_code=201,
    response_model=SessionCreateResponse,
    tags=["mayfly", "opencode", "session", "create"],
    description="Starts a session with its own OpenCode web container.",
)
async def create_session(request: Request) -> SessionCreateResponse:
    manager: SessionManager = request.app.state.manager

    try:
        session = await manager.create()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return SessionCreateResponse(
        token=session.token,
        url=f"http://{request.url.hostname}:{session.container_info.port}/",
    )


@router.get(
    path="/status",
    response_model=SessionStatusResponse,
    tags=["mayfly", "opencode", "session", "status"],
    description="Returns the number of open, free, and maximum containers.",
)
async def get_status(request: Request) -> SessionStatusResponse:
    manager: SessionManager = request.app.state.manager
    manager_status = manager.status()
    return SessionStatusResponse.model_validate(manager_status)
