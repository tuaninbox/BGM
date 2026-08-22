from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from core.vault import VaultClient
from core.utils import to_local_time

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

@router.get("/approval/dashboard", response_class=HTMLResponse)
async def approval_dashboard(
    request: Request,
    status: str | None = None,
    device: str | None = None,
    account: str | None = None,
    requester: str | None = None,
    approver: str | None = None,
    method: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user),
):
    # Only approver roles allowed
    if not current_user or current_user.role not in ("approver", "requester_approver"):
        return RedirectResponse("/ui/login", status_code=302)

    backend_url = f"{settings.backend_url}/api/approval/requests"

    resp = await request.app.state.http_client.get(
        backend_url,
        params={
            "status": status,
            "device": device,
            "account": account,
            "requester": requester,
            "approver": approver,
            "method": method,
        },
        cookies=request.cookies,
    )

    api_data = resp.json()
    requests = api_data.get("requests", [])

    # Convert timestamps to local time
    for r in requests:
        r["start_time"] = to_local_time(r["start_time"]) if r.get("start_time") else None
        r["end_time"] = to_local_time(r["end_time"]) if r.get("end_time") else None
        r["created_at"] = to_local_time(r["created_at"]) if r.get("created_at") else None
        r["approved_at"] = to_local_time(r["approved_at"]) if r.get("approved_at") else None

    # Stats
    stats_stmt = select(
        func.count().filter(BreakglassRequest.status == "pending"),
        func.count().filter(BreakglassRequest.status == "approved"),
        func.count().filter(BreakglassRequest.status == "rejected"),
        func.count().filter(BreakglassRequest.status == "expired"),
    )
    stats_result = await db.execute(stats_stmt)
    pending, approved, rejected, expired = stats_result.one()

    context = {
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
        "account": account,
        "requester": requester,
        "approver": approver,
        "method": method,
    }

    # HTMX partial refresh → only return table
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/approval_table.html", context)

    # Full page load
    return templates.TemplateResponse("dashboard.html", context)
