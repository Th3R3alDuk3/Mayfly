from fastmcp.exceptions import ToolError
from fastmcp.tools import tool

from models.session import SessionLink
from services.session import SessionError, open_session, session_url


@tool(
    name="create_mayfly_session",
    description=(
        "Start a disposable coding sandbox with a browser UI (OpenChamber on "
        "OpenCode) and return its link and password. Give the user both. "
        "The sandbox is destroyed once the browser tab is closed."
    ),
)
async def create_session() -> SessionLink:

    try:
        session = await open_session()
    # Anything else is logged and masked by FastMCP.
    except SessionError as error:
        raise ToolError(str(error)) from error

    return SessionLink(url=session_url(session.token), password=session.password)
