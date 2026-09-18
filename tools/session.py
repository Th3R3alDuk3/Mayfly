from fastmcp.exceptions import ToolError
from fastmcp.tools import tool

from models.session import Session
from services import session


@tool(
    name="create_mayfly_session",
    tags={"mayfly", "session"},
    description=(
        "Start a disposable coding sandbox with a browser UI (OpenChamber on "
        "OpenCode) and return its link and password. Give the user both. "
        "The sandbox is destroyed once the browser tab is closed."
    ),
)
async def create_session() -> Session:

    try:
        token, password = await session.create()
    except RuntimeError as error:
        raise ToolError(str(error)) from error
    except Exception as error:
        raise ToolError(
            "Could not start the sandbox. Try again later."
        ) from error

    return Session(url=session.url(token), password=password)
