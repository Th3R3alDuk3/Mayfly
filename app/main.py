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


STATIC_DIR = StaticFiles(directory="app/static", html=True)


basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.manager = SessionManager(get_settings())
    yield
    await app.state.manager.close_all()


app = FastAPI(title="Mayfly")
app.include_router(session.router)
app.include_router(lifecycle.router)

mcp = FastMCP.from_fastapi(app=app, name="Mayfly MCP")
mcp_app = mcp.http_app(path="/", transport="streamable-http")

app.router.lifespan_context = combine_lifespans(lifespan, mcp_app.lifespan)
app.mount(path="/mcp", app=mcp_app, name="mcp")
app.mount(path="/", app=STATIC_DIR, name="static")
