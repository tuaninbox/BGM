from pydantic import BaseModel
from typing import List, Optional

class AccountBase(BaseModel):
    username: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    role: str = "user"
    tenant: str
    source: str = "local"
    profiles: Optional[List[str]] = None
    otp_enabled: bool = False

class AccountRead(AccountBase):
    id: int
    # username: str
    # first_name: Optional[str] = None
    # last_name: Optional[str] = None
    # email: Optional[str] = None
    # role: str = "user"
    # source: str = "local"
    # profiles: Optional[List[str]] = None

    class Config:
        from_attributes = True

class AccountCreate(AccountBase):
    password: str
    confirm_password: str

    class Config:
        from_attributes = True

# class AccountUpdate(BaseModel):
#     username: Optional[str] = None
#     first_name: Optional[str] = None
#     last_name: Optional[str] = None
#     email: Optional[str] = None
#     role: Optional[str] = None
#     source: Optional[str] = None
#     profiles: Optional[List[str]] = None

#     new_password: Optional[str] = None
#     confirm_password: Optional[str] = None

#     class Config:
#         from_attributes = True

class AccountUpdate(AccountBase):
    new_password: Optional[str] = None
    confirm_password: Optional[str] = None

    class Config:
        from_attributes = True

class AccountSelfUpdate(BaseModel):
    username: str | None = None
    email: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    current_password: str | None = None
    new_password: str | None = None
    confirm_password: str | None = None

class OTPVerify(BaseModel):
    code: str