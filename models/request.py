from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Boolean
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

    status = Column(String, default="pending")  # pending / approved / rejected / expired / closed

    created_at = Column(String, default=lambda: datetime.now(timezone.utc).isoformat())
    approved_at = Column(String, nullable=True)
    approval_method = Column(String, nullable=True)  # direct / otp / email
    used_at = Column(String, nullable=True)

    rotation_status = Column(String, default="not_required")  # not_required / pending / queued / running / success / failed
    rotation_first_error = Column(String, nullable=True)
    rotation_failure_notified = Column(Boolean, default=False)
    rotation_error = Column(String, nullable=True)
    rotation_at = Column(String, nullable=True)  # ISO-8601 UTC timestamp



# Request status
# Status	Meaning
# pending	waiting for approval
# approved	approved but not used
# used	password has been shown/copied
# closed	user ended session
# expired	pending/approved but never used, time passed
# rejected  request was rejected

# Rotation status
# Status	Meaning
# unused	rotation not needed yet (default)
# pending	rotation should run soon (queued by cleanup or close)
# queued	rotation worker has accepted the job
# running	rotation in progress
# success	rotation completed
# failed	rotation failed
