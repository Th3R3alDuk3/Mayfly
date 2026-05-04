from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import INFO, basicConfig, getLogger

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi_offline import FastAPIOffline
from fastmcp import FastMCP
from fastmcp.utilities.lifespan import combine_lifespans
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.routers import sessions, view
from app.routers.view import http_exception_handler
from app.services.session import SessionManager

basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    logger.info(
        f"Mayfly up — listen :{settings.app_port}, "
        f"public http://{settings.public_host}:{settings.app_port}"
    )
    manager = SessionManager(settings)
    await manager.remove_managed_sandboxes()
    app.state.manager = manager
    try:
        yield
    finally:
        await app.state.manager.close_all()


app = FastAPIOffline(title="Mayfly")

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.include_router(sessions.router)
app.include_router(view.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse("app/static/logo.png", media_type="image/png")


mcp = FastMCP.from_fastapi(app=app, name="Mayfly MCP")
mcp_app = mcp.http_app(path="/", transport="streamable-http")

app.router.lifespan_context = combine_lifespans(lifespan, mcp_app.lifespan)

app.mount(path="/mcp", app=mcp_app, name="mcp")
app.mount(path="/static", app=StaticFiles(directory="app/static"), name="static")
