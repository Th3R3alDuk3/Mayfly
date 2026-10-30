from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Mount
from uvicorn import run

from routes.session import ROUTES
from services.session import session_lifespan
from tools import TOOLS

INSTRUCTIONS = "Starts private coding workspaces the user opens in the browser."

mcp_app = FastMCP(
    name="Mayfly",
    instructions=INSTRUCTIONS,
    lifespan=session_lifespan,
    tools=TOOLS,
    mask_error_details=True,
).http_app(path="/", stateless_http=True)

app = Starlette(
    routes=[Mount("/mcp", app=mcp_app), *ROUTES],
    lifespan=mcp_app.lifespan,
)


if __name__ == "__main__":
    run(app, host="0.0.0.0", port=8123)
