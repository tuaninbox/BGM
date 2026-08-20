from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timezone
from core.security import decode_token
from core.db import get_db
from models.account import Account
from models.session import Session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def extract_token_from_cookie_or_header(request: Request) -> str | None:
    # Cookie first (UI)
    token = request.cookies.get("session")
    if token:
        return token

    # Authorization header fallback (API)
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth.split(" ")[1]

    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Extract token from cookie or header
    token = extract_token_from_cookie_or_header(request)
    if not token:
        raise HTTPException(401, "Missing token")

    # Decode JWT
    try:
        payload = decode_token(token)
    except Exception:
        raise HTTPException(401, "Invalid token")

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token payload")

    # Validate session in DB
    stmt = select(Session).where(Session.token == token)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(401, "Session expired or revoked")

    # Convert ISO string → aware datetime
    expires_at_dt = datetime.fromisoformat(session.expires_at)
    if expires_at_dt < datetime.now(timezone.utc):
        await db.delete(session)
        await db.commit()
        raise HTTPException(401, "Session expired")

    # Load user
    stmt = select(Account).where(Account.id == int(user_id))
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(401, "User not found")

    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Account | None:
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None


# async def get_current_user(
#     request: Request,
#     db: AsyncSession = Depends(get_db),
# ):
#     # 1. Try cookie first (UI)
#     token = request.cookies.get("session")
#     if token:
#         try:
#             payload = decode_token(token)
#             user_id = int(payload.get("sub"))
#             if not user_id:
#                 raise HTTPException(status_code=401, detail="Invalid token payload")

#             stmt = select(Account).where(Account.id == user_id)
#             result = await db.execute(stmt)
#             user = result.scalar_one_or_none()

#             if user:
#                 return user

#         except Exception as e:
#             # print("COOKIE DECODE ERROR:", e)  # TEMP DEBUG
#             raise HTTPException(status_code=401, detail="Invalid session token")

#     # 2. Fallback to Authorization header (API)
#     try:
#         token = await oauth2_scheme(request)
#         payload = decode_token(token)
#         user_id = payload.get("sub")
#         if not user_id:
#             raise HTTPException(status_code=401, detail="Invalid token payload")

#         stmt = select(Account).where(Account.id == user_id)
#         result = await db.execute(stmt)
#         user = result.scalar_one_or_none()

#         if user and user.is_active:
#             return user

#     except Exception as e:
#         # print("HEADER DECODE ERROR:", e)  # TEMP DEBUG
#         raise HTTPException(status_code=401, detail="Unauthorized")

#     raise HTTPException(status_code=401, detail="Unauthorized")
