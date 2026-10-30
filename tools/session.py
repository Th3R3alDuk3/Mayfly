from fastmcp.exceptions import ToolError
from fastmcp.tools import tool

from models.session import SessionLink
from services.session import SessionError, open_session, session_url


@tool(
    name="create_mayfly_session",
    description=(
        "Start a private coding workspace the user opens in the browser: an "
        "AI coding agent with editor, terminal and file tree on its own "
        "isolated machine. Use it when the user wants to build, run or change "
        "code there. Returns a link and a password; give the user both "
        "unchanged and never open the link yourself: it works once and the "
        "first browser to open it owns the workspace. The workspace and its "
        "files are deleted once the browser has been gone for a while or its "
        "time limit ends."
    ),
)
async def create_session() -> SessionLink:

    try:
        session = await open_session()
    # Anything else is logged and masked by FastMCP.
    except SessionError as error:
        raise ToolError(str(error)) from error

    return SessionLink(url=session_url(session), password=session.password)
