from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import suppress
from urllib.parse import urlsplit

from anyio import create_task_group
from fastmcp.utilities.logging import get_logger
from httpx2 import AsyncClient, RequestError
from httpx2.websockets import HTTPXWSException
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.types import Scope
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from wsproto.events import BytesMessage, TextMessage

from config import get_settings

logger = get_logger(__name__)

SESSION_COOKIE = "mayfly_session"

_PUBLIC_URL = urlsplit(get_settings().public_url)
_WS_MAX_MESSAGE_BYTES = 2**26
# The page has hardcoded calls, such as to models.dev, and no setting to stop them.
_CSP = b"default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:"

_NOT_FORWARDED = frozenset({
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
})


def _target(
    scope: Scope,
) -> bytes:

    # Raw: a decoded %3F would end the path. The slash ends the authority.
    path = scope["raw_path"].removeprefix(b"/")
    query = scope["query_string"]

    return b"/%b?%b" % (path, query) if query else b"/%b" % path


def _proxy_headers(
    headers: Mapping[str, str],
) -> dict[str, str]:

    forwarded = {
        key: value for key, value in headers.items()
        if key not in _NOT_FORWARDED and not key.startswith("sec-websocket-")
    }
    # Code in the sandbox must not learn what grants access to it.
    forwarded["cookie"] = "; ".join(
        cookie for cookie in headers.get("cookie", "").split("; ")
        if not cookie.startswith(f"{SESSION_COOKIE}=")
    )
    # OpenChamber checks the browser's Origin against these.
    forwarded["x-forwarded-host"] = _PUBLIC_URL.netloc
    forwarded["x-forwarded-proto"] = _PUBLIC_URL.scheme

    return forwarded


async def proxy_http(
    request: Request,
    client: AsyncClient,
    done: Callable[[], None],
) -> Response:

    try:
        upstream = await client.send(
            client.build_request(
                method=request.method,
                url=client.base_url.copy_with(raw_path=_target(request.scope)),
                headers=_proxy_headers(request.headers),
                content=request.stream(),
            ),
            stream=True,
        )
    except RequestError as error:
        done()
        logger.warning("proxy: upstream error: %s", error)
        return Response(status_code=502, content=b"Bad gateway")
    except BaseException:
        done()
        raise

    async def close() -> None:
        try:
            await upstream.aclose()
        finally:
            done()

    # A sandbox destroyed mid-stream (lifetime, crash) ends the body; that is no error.
    async def body() -> AsyncGenerator[bytes]:
        with suppress(RequestError):
            async for chunk in upstream.aiter_raw():
                yield chunk

    response = StreamingResponse(
        content=body(),
        status_code=upstream.status_code,
        background=BackgroundTask(close),
    )
    # Raw keeps repeated Set-Cookie apart; none may replace the owner's cookie.
    response.raw_headers = [
        (key, value) for key, value in upstream.headers.raw
        if key.decode().lower() not in _NOT_FORWARDED
        and value.partition(b"=")[0].strip().decode() != SESSION_COOKIE
    ]
    response.raw_headers.append((b"content-security-policy", _CSP))

    return response


async def proxy_websocket(
    websocket: WebSocket,
    client: AsyncClient,
) -> None:

    try:
        async with client.websocket(
            client.base_url.copy_with(
                scheme="ws",
                raw_path=_target(websocket.scope),
            ),
            headers=_proxy_headers(websocket.headers),
            max_message_size_bytes=_WS_MAX_MESSAGE_BYTES,
            timeout=10,
        ) as upstream:
            await websocket.accept()

            async def to_upstream() -> None:
                with suppress(HTTPXWSException):
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            break
                        if (text := message.get("text")) is not None:
                            await upstream.send_text(text)
                        elif (data := message.get("bytes")) is not None:
                            await upstream.send_bytes(data)
                group.cancel_scope.cancel()

            async def to_client() -> None:
                with suppress(HTTPXWSException, WebSocketDisconnect):
                    while True:
                        event = await upstream.receive()
                        if isinstance(event, TextMessage):
                            await websocket.send_text(event.data)
                        elif isinstance(event, BytesMessage):
                            await websocket.send_bytes(bytes(event.data))
                group.cancel_scope.cancel()

            async with create_task_group() as group:
                group.start_soon(to_upstream)
                group.start_soon(to_client)

    except (HTTPXWSException, RequestError) as error:
        logger.warning("proxy: websocket error: %r", error)
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()
