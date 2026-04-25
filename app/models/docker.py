from pydantic import BaseModel


class Container(BaseModel):
    id: str
    name: str
