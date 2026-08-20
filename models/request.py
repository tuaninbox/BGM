from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from core.db import Base
from datetime import datetime, timezone


class BreakglassRequest(Base):
    __tablename__ = "breakglass_requests"

    id = Column(Integer, primary_key=True)

    device_name = Column(String, nullable=False)

    account_username = Column(String, nullable=False)

    requester_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    requester_username = Column(String, nullable=False)

    approver_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    approver_username = Column(String, nullable=True)

    request_reason = Column(String, nullable=False)
    approve_reason = Column(String, nullable=True)

    start_time = Column(String, nullable=False)
    end_time = Column(String, nullable=False)

    status = Column(String, default="pending")  # pending / approved / rejected

    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    approved_at = Column(String, nullable=True)
    approval_method = Column(String, nullable=True)  # "direct", "otp", "email"


