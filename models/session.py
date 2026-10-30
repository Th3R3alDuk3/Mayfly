from pydantic import BaseModel, Field


class SessionLink(BaseModel):
    url: str = Field(
        description="Opens the workspace in the browser, for the first browser only.",
    )
    password: str = Field(
        description="Password the workspace asks for once it is open.",
    )
