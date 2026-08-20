from pydantic import BaseModel

class UserRead(BaseModel):
    id: int
    username: str
    email: str
    role: str

    class Config:
        from_attributes = True
