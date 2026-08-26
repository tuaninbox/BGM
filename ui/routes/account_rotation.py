from fastapi import APIRouter, Depends,Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from core.db import get_db
from models.account import Account
from deps.auth import get_current_user_optional
from core.audit_logger import log_action
from core.settings import settings
from core.permissions import has_permission
from core.utils import to_local_time

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.globals["has_permission"] = has_permission
templates.env.cache.clear()

@router.get("/rotation/jobs", response_class=HTMLResponse)
async def rotation_jobs_page(
    request: Request,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user_optional),
):

    # If token expired or user not authenticated
    if current_user is None:
        return RedirectResponse("/ui/login", status_code=302)

    roles = request.app.state.roles

    # Permission check
    if not has_permission(current_user.role, "read_background_jobs", roles):
        return RedirectResponse("/ui/login", status_code=302)

    # Call backend API
    backend_url = f"{settings.backend_url}/api/rotation/jobs"

    resp = await request.app.state.http_client.get(
        backend_url,
        params={"status": status},
        cookies=request.cookies,
    )

    api_data = resp.json()
    jobs = api_data.get("jobs", [])

    # Convert timestamps
    for j in jobs:
        j["rotation_at"] = to_local_time(j.get("rotation_at"))

    context = {
        "request": request,
        "current_user": current_user,
        "jobs": jobs,
        "status": status,
    }

    # HTMX partial refresh
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse("partials/rotation_jobs_table.html", context)

    # Full page
    return templates.TemplateResponse("rotation_jobs.html", context)
