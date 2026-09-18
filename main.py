from asyncio import create_task
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import INFO, WARNING, basicConfig, getLogger

from fastapi import FastAPI
from fastapi_offline import FastAPIOffline
from fastmcp import FastMCP
from httpx2 import AsyncClient
from uvicorn import run

from routes import router
from services import sandbox, session
from tools import TOOLS

basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
getLogger("httpx2").setLevel(WARNING)

INSTRUCTIONS = "Starts disposable coding sandboxes with a browser UI."

mcp = FastMCP(
    name="Mayfly",
    instructions=INSTRUCTIONS,
    tools=TOOLS,
    mask_error_details=True,
)
mcp_app = mcp.http_app(path="/", stateless_http=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:

    await sandbox.remove_leftovers()
    app.state.client = AsyncClient(timeout=None)
    reaper = create_task(session.reap())

    try:
        async with mcp_app.lifespan(app):
            yield
    finally:
        reaper.cancel()
        await session.close_all()
        await app.state.client.aclose()


app = FastAPIOffline(title="Mayfly", lifespan=lifespan)

app.mount(path="/mcp", app=mcp_app)
# Last: its catch-all route proxies everything else into the sandbox.
app.include_router(router)


if __name__ == "__main__":
    run(app, host="0.0.0.0", port=8123)
