from pydantic import BaseModel, Field


class Session(BaseModel):
    url: str = Field(
        description="Link to the session; opens OpenChamber in the browser.",
    )
    password: str = Field(
        description="Password OpenChamber asks for when the link is opened.",
    )
