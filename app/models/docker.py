from pydantic import BaseModel


class ContainerInfo(BaseModel):
    id: str
    port: int
