from fastapi import APIRouter, Cookie, HTTPException, Request, WebSocket
from starlette.responses import Response

from app.models.session import SessionState
from app.services.proxy import proxy_http, proxy_websocket
from app.services.sandbox import sandbox_base_url
from app.services.session import SessionManager

SESSION_COOKIE = "mayfly_session"

router = APIRouter()


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def http(
    path: str,
    request: Request,
    mayfly_session: str | None = Cookie(default=None),
) -> Response:
    manager: SessionManager = request.app.state.manager

    session = manager.get(mayfly_session)
    if not session or session.state != SessionState.READY:
        raise HTTPException(status_code=404, detail="Session not ready")

    return await proxy_http(
        request,
        base_url=sandbox_base_url(mayfly_session),
        path=path,
        client=request.app.state.proxy_client,
    )


@router.websocket("/{path:path}")
async def ws(
    path: str,
    websocket: WebSocket,
    mayfly_session: str | None = Cookie(default=None),
) -> None:
    manager: SessionManager = websocket.app.state.manager

    session = manager.get(mayfly_session)
    if not session or session.state != SessionState.READY:
        await websocket.close(code=4404)
        return

    await proxy_websocket(
        websocket,
        base_url=sandbox_base_url(mayfly_session),
        path=path,
    )
