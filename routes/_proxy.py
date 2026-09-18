from asyncio import FIRST_COMPLETED, CancelledError, create_task, wait
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import suppress
from logging import getLogger

from httpx2 import AsyncClient, RequestError
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = getLogger(__name__)

_HOP_BY_HOP = frozenset({
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

_WS_DROP = _HOP_BY_HOP | frozenset({
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-accept",
    "sec-websocket-protocol",
})


def _proxy_headers(
    headers: Mapping[str, str],
    drop: frozenset[str],
    scheme: str,
) -> dict[str, str]:

    forwarded = {key: value for key, value in headers.items() if key.lower() not in drop}
    # OpenChamber checks the browser's Origin against these.
    forwarded["x-forwarded-host"] = headers.get("host", "")
    forwarded["x-forwarded-proto"] = scheme

    return forwarded


async def proxy_http(
    request: Request,
    port: int,
    path: str,
    client: AsyncClient,
    done: Callable[[], None],
) -> Response:

    upstream_request = client.build_request(
        method=request.method,
        url=str(request.url.replace(scheme="http", hostname="127.0.0.1", port=port, path=f"/{path}")),
        headers=_proxy_headers(request.headers, _HOP_BY_HOP, request.url.scheme),
        content=request.stream(),
    )

    try:
        upstream = await client.send(upstream_request, stream=True)
    except RequestError as error:
        done()
        logger.warning(f"Proxy upstream error: {error}")
        return Response(status_code=502, content=b"Bad gateway")
    except BaseException:
        done()
        raise

    async def close() -> None:
        await upstream.aclose()
        done()

    # The VM may be destroyed mid-stream (lifetime, crash); that is an ending, not an error.
    async def body() -> AsyncGenerator[bytes]:
        with suppress(RequestError):
            async for chunk in upstream.aiter_raw():
                yield chunk

    return StreamingResponse(
        content=body(),
        status_code=upstream.status_code,
        headers={
            key: value for key, value in upstream.headers.items() if key.lower() not in _HOP_BY_HOP
        },
        background=BackgroundTask(close),
    )


async def proxy_websocket(
    websocket: WebSocket,
    port: int,
) -> None:

    try:
        async with ws_connect(
            uri=str(websocket.url.replace(scheme="ws", hostname="127.0.0.1", port=port)),
            additional_headers=_proxy_headers(
                websocket.headers, _WS_DROP, "https" if websocket.url.scheme == "wss" else "http"
            ),
            max_size=None,
            open_timeout=10,
        ) as upstream:
            await websocket.accept()

            async def to_upstream() -> None:
                with suppress(WebSocketDisconnect, ConnectionClosed):
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        payload = message.get("bytes") or message.get("text")
                        if payload is not None:
                            await upstream.send(payload)

            async def to_client() -> None:
                with suppress(WebSocketDisconnect, ConnectionClosed):
                    async for frame in upstream:
                        if isinstance(frame, bytes):
                            await websocket.send_bytes(frame)
                        else:
                            await websocket.send_text(frame)

            tasks = {create_task(to_upstream()), create_task(to_client())}
            _, pending = await wait(tasks, return_when=FIRST_COMPLETED)

            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(CancelledError):
                    await task

    except (WebSocketException, WebSocketDisconnect, OSError) as error:
        logger.warning(f"WebSocket proxy error: {error}")
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            with suppress(RuntimeError, WebSocketDisconnect):
                await websocket.close()
