# ui/routes/logs.py
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
import httpx

from deps.auth import get_current_user_optional
from models.account import Account
from core.audit_logger import log_action
from core.settings import settings
from datetime import datetime
from zoneinfo import ZoneInfo
from core.permissions import has_permission

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.globals["has_permission"] = has_permission
templates.env.cache.clear()

api_base_url = settings.backend_url  # Use the backend URL from settings


PERTH_TZ = ZoneInfo("Australia/Perth")

def convert_log_timestamp(log):
    try:
        utc_dt = datetime.fromisoformat(log["timestamp"])
        local_dt = utc_dt.astimezone(PERTH_TZ)
        log["timestamp"] = local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return log

@router.get("/logs", response_class=HTMLResponse)
async def ui_logs_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    user: str | None = None,
    category: str | None = None,
    action: str | None = None,
    timestamp: str | None = None,
    search: str | None = None,
    limit: int = 300,
    show_filters: bool | None = None
):
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user,
            "logs_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="logs"
        )
        return RedirectResponse("/ui/login")

    # Call backend API
    async with httpx.AsyncClient() as client:
        api_response = await client.get(
            f"{api_base_url}/api/logs/",
            params={
                "user": user,
                "category": category,
                "action": action,
                "timestamp": timestamp,
                "search": search,
                "limit": limit,
            },
            cookies=request.cookies,
        )

    logs = api_response.json()
    logs = [convert_log_timestamp(log) for log in logs]

    log_action(
        current_user,
        "logs_view",
        "Viewed audit logs",
        request,
        category="logs"
    )

    # if request.headers.get("HX-Request") == "true":
    #     return templates.TemplateResponse(
    #         "partials/log_table.html",
    #         {
    #             "request": request,
    #             "logs": logs,
    #             "filter_user": user,
    #             "filter_category": category,
    #             "filter_action": action,
    #             "filter_timestamp": timestamp,
    #             "filter_search": search,
    #         }
    #     )

    return templates.TemplateResponse(
        "logs.html",
        {
            "request": request,
            "logs": logs,
            "filter_user": user,
            "filter_category": category,
            "filter_action": action,
            "filter_search": search,
            "filter_timestamp": timestamp,
            "show_filters": show_filters,
            "current_user": current_user,
        },
    )
