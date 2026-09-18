from logging import getLogger
from pathlib import PurePosixPath

from fastapi import APIRouter, Cookie, HTTPException, Request, Response, WebSocket

from config import get_settings
from models.session import Session
from services import session
from services.proxy import proxy_http, proxy_websocket
from services.session import Entry

_settings = get_settings()

logger = getLogger(__name__)

COOKIE = "mayfly_session"

UPLOAD_PATH = "api/fs/upload"
# OpenChamber caps the size but not the type, so the extensions are filtered here.
UPLOAD_EXTENSIONS = frozenset(
    f".{extension.strip().lstrip('.').lower()}"
    for extension in _settings.sandbox_upload_extensions.split(",")
    if extension.strip()
)

router = APIRouter()


@router.get(
    path="/",
    operation_id="get_mayfly_session",
    description="Returns this browser's session, starting one if none is open.",
)
async def index(
    response: Response,
    mayfly_session: str | None = Cookie(default=None),
) -> Session:

    if mayfly_session and (entry := session.get(mayfly_session)) is not None:
        return Session(url=session.url(mayfly_session), password=entry.password)

    token, password = await _create()
    response.set_cookie(key=COOKIE, value=token, path="/", httponly=True, samesite="lax")

    return Session(url=session.url(token), password=password)


@router.post(
    path="/sessions",
    status_code=201,
    operation_id="create_mayfly_session",
    description="Starts a new session and returns its link and password.",
)
async def create_session() -> Session:

    token, password = await _create()

    return Session(url=session.url(token), password=password)


@router.get(
    path="/s/{token}",
    include_in_schema=False,
)
async def open_session(
    token: str,
    request: Request,
) -> Response:

    if (entry := session.get(token)) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # OpenChamber serves its app for any path; from here on the cookie routes the origin root into the VM.
    response = await _proxy(request, entry, "")
    response.set_cookie(key=COOKIE, value=token, path="/", httponly=True, samesite="lax")

    return response


@router.api_route(
    path="/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def http(
    path: str,
    request: Request,
    mayfly_session: str | None = Cookie(default=None),
) -> Response:

    if (entry := session.get(mayfly_session)) is None:
        raise HTTPException(status_code=404, detail="Session not found")

    if (
        path == UPLOAD_PATH
        and UPLOAD_EXTENSIONS
        and PurePosixPath(request.query_params.get("path", "")).suffix.lower() not in UPLOAD_EXTENSIONS
    ):
        raise HTTPException(
            status_code=415,
            detail=f"Allowed file types: {', '.join(sorted(UPLOAD_EXTENSIONS))}",
        )

    return await _proxy(request, entry, path)


@router.websocket(
    path="/{path:path}",
)
async def websocket(
    path: str,
    websocket: WebSocket,
    mayfly_session: str | None = Cookie(default=None),
) -> None:

    if (entry := session.get(mayfly_session)) is None:
        await websocket.close(code=4404)
        return

    session.connect(entry)
    try:
        await proxy_websocket(websocket, entry.machine.port, path)
    finally:
        session.disconnect(entry)


async def _create() -> tuple[str, str]:

    try:
        return await session.create()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except Exception as error:
        logger.exception("Sandbox start failed")
        raise HTTPException(
            status_code=503,
            detail="Could not start the sandbox. Try again later.",
        ) from error


async def _proxy(
    request: Request,
    entry: Entry,
    path: str,
) -> Response:

    session.connect(entry)

    return await proxy_http(
        request,
        port=entry.machine.port,
        path=path,
        client=request.app.state.client,
        done=lambda: session.disconnect(entry),
    )
