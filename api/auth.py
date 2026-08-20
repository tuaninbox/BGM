from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from datetime import datetime, timedelta, timezone
from core.audit_logger import log_action
from core.db import get_db
from core.security import verify_password, create_access_token
from models.account import Account
from models.session import Session
from deps.auth import get_current_user_optional, extract_token_from_cookie_or_header
from schemas.auth import LoginRequest, Token
from core.security import (
    too_many_attempts,
    record_failed_attempt,
    clear_attempts
)



router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login", response_model=Token)
async def login(
    data: LoginRequest,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db)
):
    ip = request.client.host

    if not data.username or not data.password:
        raise HTTPException(400, "Username and Password are required")

    username = data.username

    if await too_many_attempts(ip, username):
        log_action(
            current_user.username if current_user else None,
            "login_attempt",
            f"Too many failed login attempts for username: {username}",
            request,
            category="authentication",
        )
        raise HTTPException(429, "Too many failed login attempts. Try again later.")

    stmt = select(Account).where(Account.username == username)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        record_failed_attempt(ip, username)
        log_action(
            current_user.username if current_user else None,
            "login_attempt",
            f"Invalid credentials for username: {username}",
            request,
            category="authentication",
        )
        raise HTTPException(401, "Invalid credentials")

    clear_attempts(ip, username)

    log_action(
        current_user.username if current_user else None,
        "login_success",
        f"Successful Login for username: {username}",
        request,
        category="authentication",
    )

    token = create_access_token({"sub": str(user.id), "role": user.role})

    # Store session in DB
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()

    session = Session(
        token=token,
        user_id=user.id,
        expires_at=expires_at
    )
    db.add(session)
    await db.commit()

    expires_at_dt = datetime.fromisoformat(session.expires_at)

    if expires_at_dt < datetime.now(timezone.utc):
        await db.delete(session)
        await db.commit()

    # Backend returns JSON only — frontend sets cookie
    return Token(access_token=token, token_type="bearer")

@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)):
    token = extract_token_from_cookie_or_header(request)
    print("Backend Logout TOKEN RECEIVED:", token)

    if token:
        stmt = select(Session).where(Session.token == token)
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()

        if session:
            await db.delete(session)
            await db.commit()

    return {"message": "Logged out"}
