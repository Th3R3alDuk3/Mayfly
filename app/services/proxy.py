from asyncio import FIRST_COMPLETED, CancelledError, create_task, wait
from collections.abc import Mapping
from contextlib import suppress
from logging import getLogger
from typing import Final

from httpx import AsyncClient, RequestError
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import ClientConnection, connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException

logger = getLogger(__name__)


_HOP_BY_HOP: Final[frozenset[str]] = frozenset({
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

_WS_DROP: Final[frozenset[str]] = _HOP_BY_HOP | frozenset({
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-accept",
    "sec-websocket-protocol",
})


def _forward_headers(headers: Mapping[str, str], drop: frozenset[str]) -> dict[str, str]:
    # Strip hop-by-hop and protocol-specific headers a proxy must not forward.
    return {key: value for key, value in headers.items() if key.lower() not in drop}


def _join_url(base_url: str, path: str, query: str) -> str:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    return f"{url}?{query}" if query else url


async def proxy_http(
    request: Request,
    *,
    base_url: str,
    path: str,
    client: AsyncClient,
) -> Response:
    upstream_request = client.build_request(
        method=request.method,
        url=_join_url(base_url, path, request.url.query),
        headers=_forward_headers(request.headers, _HOP_BY_HOP),
        content=request.stream(),
    )

    try:
        upstream = await client.send(upstream_request, stream=True)
    except RequestError as error:
        logger.warning(f"Proxy upstream error: {error}")
        return Response(status_code=502, content=b"Bad gateway")

    return StreamingResponse(
        content=upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=_forward_headers(upstream.headers, _HOP_BY_HOP),
        background=BackgroundTask(upstream.aclose),
    )


async def proxy_websocket(
    websocket: WebSocket,
    *,
    base_url: str,
    path: str,
) -> None:
    upstream_url = _join_url(base_url, path, websocket.url.query)
    ws_url = upstream_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)

    requested = websocket.headers.get("sec-websocket-protocol", "")
    subprotocols = [p.strip() for p in requested.split(",") if p.strip()] or None

    try:
        with suppress(InvalidStatus, WebSocketException, WebSocketDisconnect):
            async with ws_connect(
                uri=ws_url,
                additional_headers=_forward_headers(websocket.headers, _WS_DROP),
                subprotocols=subprotocols,
                max_size=None,
                open_timeout=10,
            ) as upstream:
                await websocket.accept(subprotocol=upstream.subprotocol)
                await _pipe(websocket, upstream)
    except Exception:
        logger.exception(f"WebSocket proxy error for {ws_url}")
    finally:
        if websocket.application_state != WebSocketState.DISCONNECTED:
            await websocket.close()


async def _pipe(client_ws: WebSocket, upstream_ws: ClientConnection) -> None:
    async def to_upstream() -> None:
        with suppress(WebSocketDisconnect, ConnectionClosed):
            while True:
                message = await client_ws.receive()
                if message["type"] == "websocket.disconnect":
                    await upstream_ws.close()
                    return
                payload = message.get("bytes") or message.get("text")
                if payload is not None:
                    await upstream_ws.send(payload)

    async def to_client() -> None:
        with suppress(ConnectionClosed):
            async for frame in upstream_ws:
                if isinstance(frame, bytes):
                    await client_ws.send_bytes(frame)
                else:
                    await client_ws.send_text(frame)

    tasks = {create_task(to_upstream()), create_task(to_client())}
    _, pending = await wait(tasks, return_when=FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        with suppress(CancelledError):
            await task
