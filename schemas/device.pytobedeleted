from pydantic import BaseModel

class DeviceRead(BaseModel):
    id: int
    name: str
    description: str | None = None

    class Config:
        from_attributes = True

class DeviceCreate(BaseModel):
    name: str
    description: str | None = None

class DeviceImportItem(BaseModel):
    name: str
    description: str | None = None
