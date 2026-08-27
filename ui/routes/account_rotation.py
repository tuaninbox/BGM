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

# @router.get("/rotation/jobs", response_class=HTMLResponse)
# async def rotation_jobs_page(
#     request: Request,
#     status: str | None = None,
#     db: AsyncSession = Depends(get_db),
#     current_user: Account = Depends(get_current_user_optional),
# ):

#     # If token expired or user not authenticated
#     if current_user is None:
#         return RedirectResponse("/ui/login", status_code=302)

#     roles = request.app.state.roles

#     # Permission check
#     if not has_permission(current_user.role, "read_rotation_jobs", roles):
#         return RedirectResponse("/ui/login", status_code=302)

#     # Call backend API
#     backend_url = f"{settings.backend_url}/api/rotation/jobs"

#     resp = await request.app.state.http_client.get(
#         backend_url,
#         params={"status": status},
#         cookies=request.cookies,
#     )

#     api_data = resp.json()
#     jobs = api_data.get("jobs", [])

#     # Convert timestamps
#     for j in jobs:
#         j["rotation_at"] = to_local_time(j.get("rotation_at"))

#     context = {
#         "request": request,
#         "current_user": current_user,
#         "jobs": jobs,
#         "status": status,
#     }

#     # HTMX partial refresh
#     if request.headers.get("HX-Request"):
#         return templates.TemplateResponse("partials/rotation_jobs_table.html", context)

#     # Full page
#     return templates.TemplateResponse("rotation_jobs.html", context)

@router.get("/rotation/jobs", response_class=HTMLResponse)
async def rotation_jobs_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    page: int = 1,
    page_size: str = "20",
    sort_by: str = "rotation_at",
    sort_dir: str = "desc",
    status: str | None = None,
):
    # -----------------------------------------
    # Not logged in → redirect
    # -----------------------------------------
    if current_user is None:
        return RedirectResponse("/ui/login")

    roles = request.app.state.roles

    # -----------------------------------------
    # Permission check
    # -----------------------------------------
    if not has_permission(current_user.role, "read_rotation_jobs", roles):
        return RedirectResponse("/ui/login")

    # -----------------------------------------
    # Page size parsing
    # -----------------------------------------
    try:
        if page_size == "custom":
            page_size_int = 20  # fallback
        else:
            page_size_int = int(page_size)
    except ValueError:
        page_size_int = 20

    # -----------------------------------------
    # Build backend API URL
    # -----------------------------------------
    backend_url = (
        f"{settings.backend_url}/api/rotation/jobs"
        f"?page={page}&page_size={page_size_int}"
        f"&sort_by={sort_by}&sort_dir={sort_dir}"
    )

    # Always include status, even if empty string
    if status is not None:
        backend_url += f"&status={status}"

    # -----------------------------------------
    # Call backend API
    # -----------------------------------------
    resp = await request.app.state.http_client.get(
        backend_url,
        cookies=request.cookies,
    )

    # -----------------------------------------
    # Parse JSON safely
    # -----------------------------------------
    try:
        data = resp.json()
    except Exception:
        return templates.TemplateResponse(
            "rotation_jobs.html",
            {
                "request": request,
                "current_user": current_user,
                "jobs": [],
                "error": "Backend returned invalid JSON",
                "page": page,
                "page_size": page_size_int,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "total": 0,
                "status": status,
            },
            status_code=500,
        )

    # -----------------------------------------
    # Backend error
    # -----------------------------------------
    if not data.get("ok"):
        return templates.TemplateResponse(
            "rotation_jobs.html",
            {
                "request": request,
                "current_user": current_user,
                "jobs": [],
                "error": data.get("error"),
                "page": page,
                "page_size": page_size_int,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "total": 0,
                "status": status,
            },
            status_code=500,
        )

    # -----------------------------------------
    # Extract jobs + total
    # -----------------------------------------
    jobs = data.get("jobs", [])
    total = data.get("total", 0)

    # Convert timestamps
    for j in jobs:
        j["rotation_at"] = to_local_time(j.get("rotation_at"))

    context = {
        "request": request,
        "current_user": current_user,
        "jobs": jobs,
        "total": total,
        "page": page,
        "page_size": page_size_int,
        "sort_by": sort_by,
        "sort_dir": sort_dir,
        "status": status,  # <-- IMPORTANT
    }

    # -----------------------------------------
    # HTMX partial load
    # -----------------------------------------
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/rotation_jobs_table.html",
            context,
        )

    # -----------------------------------------
    # Full page load
    # -----------------------------------------
    return templates.TemplateResponse(
        "rotation_jobs.html",
        context,
    )
