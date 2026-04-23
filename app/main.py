from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import INFO, basicConfig
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import lifecycle, session
from app.services.session import SessionManager


basicConfig(level=INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.manager = SessionManager(get_settings())
    yield
    await app.state.manager.close_all()


app = FastAPI(title="Mayfly", lifespan=lifespan)
app.include_router(session.router)
app.include_router(lifecycle.router)

app.mount(
    path="/",
    app=StaticFiles(directory="app/static", html=True),
    name="static",
)
