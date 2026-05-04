from pydantic import BaseModel


class Sandbox(BaseModel):
    id: str
    port: int
