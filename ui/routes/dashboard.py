from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from core.vault import VaultClient
from core.utils import to_local_time

from core.db import get_db
from deps.auth import get_current_user, get_current_user_optional
from core.security import verify_password, create_access_token, hash_password
from models.account import Account
from models.request import BreakglassRequest
from core.device_loader import load_devices
from core.audit_logger import log_action
from core.settings import settings
from core.debug import debug_error
from core.permissions import has_permission

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.globals["has_permission"] = has_permission
templates.env.cache.clear()

@router.get("/approval/dashboard", response_class=HTMLResponse)
async def approval_dashboard(
    request: Request,
    status: str | None = None,
    device: str | None = None,
    account: str | None = None,
    requester: str | None = None,
    approver: str | None = None,
    method: str | None = None,
    rotation: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user_optional),
):
    # Only approver roles allowed
    if not current_user or current_user.role not in ("approver", "requester_approver"):
        return RedirectResponse("/ui/login", status_code=302)

    backend_url = f"{settings.backend_url}/api/approval/requests"

    # Fetch filtered requests from backend API
    resp = await request.app.state.http_client.get(
        backend_url,
        params={
            "status": status,
            "device": device,
            "account": account,
            "requester": requester,
            "approver": approver,
            "method": method,
            "rotation": rotation,
        },
        cookies=request.cookies,
    )

    api_data = resp.json()
    requests = api_data.get("requests", [])

    # Convert timestamps to local time
    for r in requests:
        r["start_time"] = to_local_time(r.get("start_time"))
        r["end_time"] = to_local_time(r.get("end_time"))
        r["created_at"] = to_local_time(r.get("created_at"))
        r["approved_at"] = to_local_time(r.get("approved_at"))
        r["rotation_at"] = to_local_time(r.get("rotation_at"))

    total_stmt = select(func.count()).select_from(BreakglassRequest)
    total_result = await db.execute(total_stmt)
    total = total_result.scalar()

    # ---------------------------------------------------------
    # FULL STATUS COUNTS
    # ---------------------------------------------------------
    status_counts_stmt = select(
        func.count().filter(BreakglassRequest.status == "pending"),
        func.count().filter(BreakglassRequest.status == "approved"),
        func.count().filter(BreakglassRequest.status == "used"),
        func.count().filter(BreakglassRequest.status == "closed"),
        func.count().filter(BreakglassRequest.status == "rejected"),
        func.count().filter(BreakglassRequest.status == "expired"),
    )

    status_result = await db.execute(status_counts_stmt)
    (
        pending,
        approved,
        used,
        closed,
        rejected,
        expired,
    ) = status_result.one()

    # ---------------------------------------------------------
    # FULL ROTATION STATUS COUNTS
    # ---------------------------------------------------------
    rotation_counts_stmt = select(
        func.count().filter(BreakglassRequest.rotation_status == "not_required"),
        func.count().filter(BreakglassRequest.rotation_status == "pending"),
        func.count().filter(BreakglassRequest.rotation_status == "queued"),
        func.count().filter(BreakglassRequest.rotation_status == "running"),
        func.count().filter(BreakglassRequest.rotation_status == "success"),
        func.count().filter(BreakglassRequest.rotation_status == "failed"),
    )

    rotation_result = await db.execute(rotation_counts_stmt)
    (
        rot_not_required,
        rot_pending,
        rot_queued,
        rot_running,
        rot_success,
        rot_failed,
    ) = rotation_result.one()

    # ---------------------------------------------------------
    # Context for template
    # ---------------------------------------------------------
    context = {
        "request": request,
        "current_user": current_user,
        "requests": requests,

        # All status counts
        "stats": {
            "total": total,
            "pending": pending,
            "approved": approved,
            "used": used,
            "closed": closed,
            "rejected": rejected,
            "expired": expired,
        },

        # All rotation counts
        "rotation_stats": {
            "not_required": rot_not_required,
            "pending": rot_pending,
            "queued": rot_queued,
            "running": rot_running,
            "success": rot_success,
            "failed": rot_failed,
        },

        # Filters
        "status": status,
        "device": device,
        "account": account,
        "requester": requester,
        "approver": approver,
        "method": method,
        "rotation": rotation,
    }

    # HTMX partial refresh → only return table
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/approval_table.html", context)

    # Full page load
    return templates.TemplateResponse("dashboard.html", context)
