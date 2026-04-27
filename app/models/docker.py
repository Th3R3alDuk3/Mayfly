from pydantic import BaseModel


class Container(BaseModel):
    id: str
    port: int
