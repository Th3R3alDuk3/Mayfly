from pathlib import PurePosixPath

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

from config import get_settings
from models.session import SessionLink
from routes._proxy import SESSION_COOKIE, proxy_http, proxy_websocket
from services.session import (
    Session,
    SessionError,
    claim_session,
    connect_session,
    disconnect_session,
    get_session,
    open_session,
    session_url,
)

_settings = get_settings()

_UPLOAD_PATH = "api/fs/upload"
# OpenChamber caps the size but not the type, so the extensions are filtered here.
_UPLOAD_EXTENSIONS = frozenset(
    f".{extension.strip().lstrip('.').lower()}"
    for extension in _settings.sandbox_upload_extensions.split(",")
    if extension.strip()
)


async def _open(
    status_code: int,
) -> Response:

    try:
        session = await open_session()
    except SessionError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error

    return JSONResponse(
        content=SessionLink(
            url=session_url(session),
            password=session.password,
        ).model_dump(),
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


async def _proxied(
    request: Request,
    session: Session,
) -> Response:

    connect_session(session)

    return await proxy_http(
        request,
        client=session.client,
        done=lambda: disconnect_session(session),
    )


async def browser_session(
    request: Request,
) -> Response:

    if (session := get_session(request.cookies.get(SESSION_COOKIE))) is not None:
        return await _proxied(request, session)

    # A monitor or link checker must not start a sandbox.
    if request.method == "HEAD":
        return Response()

    return await _open(200)


async def create_session(
    request: Request,
) -> Response:

    return await _open(201)


async def enter_session(
    request: Request,
) -> Response:

    # A link checker must not use up the link.
    if request.method == "HEAD":
        return Response()

    link = request.path_params["link"]
    token = request.cookies.get(SESSION_COOKIE)

    # A used link only brings its owner back, password or not.
    if (session := claim_session(link) or get_session(token)) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session.token,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_settings.public_url.startswith("https://"),
    )

    return response


async def catch_all(
    request: Request,
) -> Response:

    if (session := get_session(request.cookies.get(SESSION_COOKIE))) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # OpenChamber routes case-insensitively and ignores a trailing slash.
    path = request.path_params["path"].strip("/").lower()

    if path == _UPLOAD_PATH and _UPLOAD_EXTENSIONS:
        suffix = PurePosixPath(request.query_params.get("path", "")).suffix.lower()
        if suffix not in _UPLOAD_EXTENSIONS:
            raise HTTPException(
                status_code=415,
                detail=f"Allowed file types: {', '.join(sorted(_UPLOAD_EXTENSIONS))}",
            )

    return await _proxied(request, session)


async def catch_all_websocket(
    websocket: WebSocket,
) -> None:

    if (session := get_session(websocket.cookies.get(SESSION_COOKIE))) is None:
        await websocket.close()
        return

    connect_session(session)
    try:
        await proxy_websocket(websocket, session.client)
    finally:
        disconnect_session(session)


# Last: the catch-all routes proxy everything else into the sandbox.
ROUTES = (
    Route("/", browser_session),
    Route("/sessions", create_session, methods=["POST"]),
    Route("/s/{link}", enter_session),
    Route(
        "/{path:path}",
        catch_all,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    ),
    WebSocketRoute("/{path:path}", catch_all_websocket),
)
