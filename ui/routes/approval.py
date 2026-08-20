from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from core.vault import VaultClient

from core.db import get_db
from deps.auth import get_current_user
from core.security import verify_password, create_access_token, hash_password
from models.account import Account
from models.request import BreakglassRequest
from core.device_loader import load_devices
from core.audit_logger import log_action
from core.settings import settings
from core.debug import debug_error

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.cache.clear()

@router.get("/approver/dashboard", response_class=HTMLResponse)
async def approver_dashboard(
    request: Request,
    status: str | None = None,
    device: str | None = None,
    requester: str | None = None,
    method: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user),  # your get_current_user
):
    if current_user.role not in ("approver", "requester_approver"):
        return request.app.state.templates.TemplateResponse(
            "error.html",
            {"request": request, "error": "Forbidden"},
        )

    stmt = select(BreakglassRequest)

    if status:
        stmt = stmt.where(BreakglassRequest.status == status)
    if device:
        stmt = stmt.where(BreakglassRequest.device_name.ilike(f"%{device}%"))
    if requester:
        stmt = stmt.where(BreakglassRequest.requester.ilike(f"%{requester}%"))
    if method:
        stmt = stmt.where(BreakglassRequest.approval_method == method)

    result = await db.execute(stmt.order_by(BreakglassRequest.created_at.desc()))
    requests = result.scalars().all()

    stats_stmt = select(
        func.count().filter(BreakglassRequest.status == "pending"),
        func.count().filter(BreakglassRequest.status == "approved"),
        func.count().filter(BreakglassRequest.status == "rejected"),
        func.count().filter(BreakglassRequest.status == "expired"),
    )
    stats_result = await db.execute(stats_stmt)
    pending, approved, rejected, expired = stats_result.one()

    return templates.TemplateResponse(
        "approval.html",
        {
            "request": request,
            "current_user": current_user,
            "requests": requests,
            "stats": {
                "pending": pending,
                "approved": approved,
                "rejected": rejected,
                "expired": expired,
            },
            "status": status,
            "device": device,
            "requester": requester,
            "method": method,
        },
    )
