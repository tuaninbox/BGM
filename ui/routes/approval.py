from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from models.request import BreakglassRequest
from models.account import Account  # adjust import to your project
from deps.auth import get_current_user
router = APIRouter()


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

    return request.app.state.templates.TemplateResponse(
        "approver/dashboard.html",
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
