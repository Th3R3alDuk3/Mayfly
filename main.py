from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_offline import FastAPIOffline
from fastmcp import FastMCP
from httpx2 import AsyncClient
from uvicorn import run

from routes.session import router
from services.session import session_lifespan
from tools import TOOLS

INSTRUCTIONS = "Starts disposable coding sandboxes with a browser UI."

mcp_app = FastMCP(
    name="Mayfly",
    instructions=INSTRUCTIONS,
    tools=TOOLS,
    mask_error_details=True,
).http_app(path="/", stateless_http=True)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncGenerator[None]:

    async with (
        # One connection pool for everything the proxy sends into the VMs.
        AsyncClient(timeout=None) as app.state.client,
        session_lifespan(),
        mcp_app.lifespan(app),
    ):
        yield


app = FastAPIOffline(title="Mayfly", lifespan=lifespan)

app.mount(path="/mcp", app=mcp_app)
# Last: its catch-all route proxies everything else into the sandbox.
app.include_router(router)


if __name__ == "__main__":
    run(app, host="0.0.0.0", port=8123)
