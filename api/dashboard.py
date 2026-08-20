from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from core.db import get_db
# from models.request import BreakglassRequest
from schemas.request import BreakglassRequestCreate, BreakglassRequestUpdate
from models.account import Account
from models.request import BreakglassRequest
from deps.auth import get_current_user
from core.debug import debug_error, debug_print
from core.vault import VaultClient
from core.email import generate_email_approval_token
from core.audit_logger import log_action
from core.settings import settings


router = APIRouter(prefix="/api", tags=["approval"])

@router.get("/approval/requests")
async def api_approval_requests(
    status: str | None = None,
    device: str | None = None,
    account: str | None = None,
    requester: str | None = None,
    approver: str | None = None,
    method: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user),
):
    if current_user.role not in ("approver", "requester_approver"):
        return {"error": "Forbidden"}

    stmt = select(BreakglassRequest)

    if status:
        stmt = stmt.where(BreakglassRequest.status == status)
    if device:
        stmt = stmt.where(BreakglassRequest.device_name.ilike(f"%{device}%"))
    if account:
        stmt = stmt.where(BreakglassRequest.account_username.ilike(f"%{account}%"))
    if requester:
        stmt = stmt.where(BreakglassRequest.requester_username.ilike(f"%{requester}%"))
    if approver:
        stmt = stmt.where(BreakglassRequest.approver_username.ilike(f"%{approver}%"))
    if method:
        stmt = stmt.where(BreakglassRequest.approval_method == method)

    result = await db.execute(stmt.order_by(BreakglassRequest.created_at.desc()))
    requests = result.scalars().all()

    # Return raw UTC timestamps (ISO8601)
    return {
        "requests": [
            {
                "id": r.id,
                "device_name": r.device_name,
                "account_username": r.account_username,
                "requester": r.requester_username,
                "approver": r.approver_username,
                "request_reason": r.request_reason,
                "approve_reason": r.approve_reason,
                "start_time": r.start_time,
                "end_time": r.end_time,
                "created_at": r.created_at,
                "approved_at": r.approved_at,
                "status": r.status,
                "approval_method": r.approval_method,
            }
            for r in requests
        ]
    }
