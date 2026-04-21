import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import lifecycle, session
from app.services.session import SessionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.manager = SessionManager(get_settings())
    yield
    await app.state.manager.close_all()


app = FastAPI(title="OpenCode Docker Manager", lifespan=lifespan)

app.include_router(session.router)
app.include_router(lifecycle.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.mount("/", StaticFiles(directory="app/static", html=True), name="static")
