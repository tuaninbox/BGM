from sqlalchemy import Column, Integer, String, Boolean
from sqlalchemy.types import JSON
from core.db import Base

class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)

    username = Column(String, nullable=False, unique=True)
    password_hash = Column(String, nullable=False)

    role = Column(String, nullable=False, default="user")  # admin, operator, user

    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    email = Column(String, nullable=True)

    source = Column(String, nullable=False, default="local")  
    # allowed: local, ldap, saml

    profiles = Column(JSON, nullable=True)  
    # e.g. ["network-admin", "read-only"]

    tenant = Column(String, nullable=False)

    otp_enabled = Column(Boolean, nullable=False, default=False)