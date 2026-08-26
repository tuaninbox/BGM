from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, UniqueConstraint
from core.db import Base


class EmailApprovalToken(Base):
    __tablename__ = "email_approval_tokens"

    id = Column(Integer, primary_key=True, index=True)
    req_id = Column(Integer, nullable=False, index=True)
    approver_id = Column(Integer, nullable=False, index=True)
    nonce = Column(String(64), nullable=False, unique=True, index=True)
    created_at = Column(String, nullable=False, default=lambda: datetime.now(timezone.utc).isoformat())
    expiry_time = Column(String, nullable=False)
    used_at = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("nonce", name="uq_email_approval_nonce"),
    )
