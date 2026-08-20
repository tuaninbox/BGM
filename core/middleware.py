from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from jose import jwt, JWTError
from sqlalchemy import select

from core.db import get_db
from models.user import User
from core.audit_logger import log_action
from core.settings import settings


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Extract session cookie manually (middleware cannot use Depends)
        session_cookie = request.cookies.get("session")

        user = None

        if session_cookie:
            try:
                # Decode JWT using settings
                payload = jwt.decode(
                    session_cookie,
                    settings.jwt_secret,
                    algorithms=[settings.jwt_algorithm],
                )

                user_id = int(payload.get("sub"))

                # Load user manually (middleware cannot use Depends)
                async with get_db() as db:
                    stmt = select(User).where(User.id == user_id)
                    result = await db.execute(stmt)
                    user = result.scalar_one_or_none()

            except JWTError:
                user = None
            except Exception:
                user = None

        # Pre-request audit log
        log_action(
            user=user,
            action="http_request",
            details=f"{request.method} {request.url.path}",
            request=request,
            category="navigation",
        )

        response: Response = await call_next(request)

        # Post-request audit log
        log_action(
            user=user,
            action="http_response",
            details=f"Status {response.status_code} for {request.method} {request.url.path}",
            request=request,
            category="navigation",
        )

        return response
