from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import INFO, basicConfig
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans

from app.config import get_settings
from app.routers import lifecycle, session
from app.services.session import SessionManager


basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.manager = SessionManager(get_settings())
    yield
    await app.state.manager.close_all()


api = FastAPI(title="Mayfly", lifespan=lifespan)
api.include_router(session.router)
api.include_router(lifecycle.router)

mcp = FastMCP.from_fastapi(app=api, name="Mayfly MCP")
mcp_app = mcp.http_app(path="/mcp", transport="streamable-http")

app = FastAPI(
    title="Mayfly",
    routes=[*mcp_app.routes, *api.routes],
    lifespan=combine_lifespans(mcp_app.lifespan, lifespan),
)
app.mount(
    path="/",
    app=StaticFiles(directory="app/static", html=True),
    name="static",
)
