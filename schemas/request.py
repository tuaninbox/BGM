from datetime import datetime
from pydantic import BaseModel

class BreakglassRequestCreate(BaseModel):
    device_name: str
    account_username: str

    requester_id: int
    requester_username: str

    request_reason: str

    start_time: datetime
    end_time: datetime


class BreakglassRequestUpdate(BaseModel):
    approver_id: int | None = None
    approver_username: str | None = None
    approve_reason: str | None = None
    status: str | None = None