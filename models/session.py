from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey
from core.db import Base

class Session(Base):
    __tablename__ = "sessions"

    token = Column(String, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.now(timezone.utc))
    expires_at = Column(String)
    # expires_at = Column(DateTime(timezone=True), nullable=False)
