from pathlib import PurePosixPath
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, WebSocket

from config import get_settings
from models.session import SessionLink
from routes._proxy import proxy_http, proxy_websocket
from services.session import (
    Session,
    SessionError,
    connect_session,
    disconnect_session,
    get_session,
    open_session,
    session_url,
)

_settings = get_settings()

_COOKIE = "mayfly_session"

_UPLOAD_PATH = "api/fs/upload"
# OpenChamber caps the size but not the type, so the extensions are filtered here.
_UPLOAD_EXTENSIONS = frozenset(
    f".{extension.strip().lstrip('.').lower()}"
    for extension in _settings.sandbox_upload_extensions.split(",")
    if extension.strip()
)

router = APIRouter()


async def _open() -> Session:

    try:
        return await open_session()
    except SessionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


async def _proxied(
    request: Request,
    session: Session,
    path: str,
) -> Response:

    connect_session(session)

    return await proxy_http(
        request,
        port=session.port,
        path=path,
        client=request.app.state.client,
        done=lambda: disconnect_session(session),
    )


@router.get(
    path="/",
    operation_id="get_mayfly_session",
    description="Returns this browser's session, starting one if none is open.",
)
async def browser_session(
    response: Response,
    token: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
) -> SessionLink:

    if (session := get_session(token)) is None:
        session = await _open()
        response.set_cookie(
            key=_COOKIE,
            value=session.token,
            path="/",
            httponly=True,
            samesite="lax",
        )

    return SessionLink(url=session_url(session.token), password=session.password)


@router.post(
    path="/sessions",
    status_code=201,
    operation_id="create_mayfly_session",
    description="Starts a new session and returns its link and password.",
)
async def create_session() -> SessionLink:

    session = await _open()

    return SessionLink(url=session_url(session.token), password=session.password)


@router.get(
    path="/s/{token}",
    include_in_schema=False,
)
async def enter_session(
    token: str,
    request: Request,
) -> Response:

    if (session := get_session(token)) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Any path serves the app; the cookie now routes the origin root into the sandbox.
    response = await _proxied(request, session, "")
    response.set_cookie(
        key=_COOKIE,
        value=token,
        path="/",
        httponly=True,
        samesite="lax",
    )

    return response


@router.api_route(
    path="/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def catch_all(
    path: str,
    request: Request,
    token: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
) -> Response:

    if (session := get_session(token)) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if path == _UPLOAD_PATH and _UPLOAD_EXTENSIONS:
        suffix = PurePosixPath(request.query_params.get("path", "")).suffix.lower()
        if suffix not in _UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Allowed file types: {', '.join(sorted(_UPLOAD_EXTENSIONS))}",
            )

    return await _proxied(request, session, path)


@router.websocket(
    path="/{path:path}",
)
async def catch_all_websocket(
    websocket: WebSocket,
    token: Annotated[str | None, Cookie(alias=_COOKIE)] = None,
) -> None:

    if (session := get_session(token)) is None:
        await websocket.close(code=4404)
        return

    connect_session(session)
    try:
        await proxy_websocket(websocket, session.port)
    finally:
        disconnect_session(session)
