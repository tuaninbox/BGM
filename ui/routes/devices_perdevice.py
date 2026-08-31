from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.db import get_db
from deps.auth import get_current_user, get_current_user_optional
from core.security import verify_password, create_access_token, hash_password
from models.account import Account
from core.device_loader import load_devices
from core.audit_logger import log_action
from core.settings import settings
from core.permissions import has_permission

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.globals["has_permission"] = has_permission
templates.env.cache.clear()

@router.get("/devices", response_class=HTMLResponse)
async def devices_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    page: int = 1,
    page_size: str = "20",
    sort_by: str = "name",
    sort_dir: str = "asc",
):
    # -----------------------------------------
    # Not logged in → redirect + log
    # -----------------------------------------
    if current_user is None:
        log_action(
            current_user,
            "device_view",
            f"Device View - Unauthorized access attempt",
            request,
            category="device",
        )
        return RedirectResponse("/ui/login")

    try:
        if page_size == "custom":
            page_size_int = 20   # fallback default
        else:
            page_size_int = int(page_size)
    except ValueError:
        page_size_int = 20
    # -----------------------------------------
    # Build backend API URL
    # -----------------------------------------
    api_url = (
        f"{settings.backend_url}/api/devices"
        f"?page={page}&page_size={page_size_int}"
        f"&sort_by={sort_by}&sort_dir={sort_dir}"
    )

    api_resp = await request.app.state.http_client.get(
        api_url,
        cookies=request.cookies
    )

    # -----------------------------------------
    # Parse JSON safely
    # -----------------------------------------
    try:
        data = api_resp.json()
    except Exception:
        log_action(
            current_user,
            "device_view",
            "Device View - Backend returned invalid JSON",
            request,
            category="device",
        )
        return templates.TemplateResponse(
            "devices.html",
            {
                "request": request,
                "current_user": current_user,
                "devices": [],
                "error": "Backend returned invalid JSON",
                "page": page,
                "page_size": page_size,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "total": 0,
            },
            status_code=500,
        )

    # -----------------------------------------
    # Backend error response
    # -----------------------------------------
    if not data.get("ok"):
        log_action(
            current_user,
            "device_view",
            f"Device View - Backend error: {data.get('error')}",
            request,
            category="device",
        )
        return templates.TemplateResponse(
            "devices.html",
            {
                "request": request,
                "current_user": current_user,
                "devices": [],
                "error": data.get("error"),
                "page": page,
                "page_size": page_size_int,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "total": 0,
            },
            status_code=500,
        )

    # -----------------------------------------
    # Extract device list
    # -----------------------------------------
    devices = data.get("devices", [])
    total = data.get("total", 0)

    # -----------------------------------------
    # HTMX partial load
    # -----------------------------------------
    if request.headers.get("HX-Request"):
        log_action(
            current_user,
            "device_view",
            f"Device View - HTMX partial load (page={page}, size={page_size_int}, sort={sort_by}:{sort_dir})",
            request,
            category="device",
        )
        return templates.TemplateResponse(
            "partials/devices_table.html",
            {
                "request": request,
                "current_user": current_user,
                "devices": devices,
                "total": total,
                "page": page,
                "page_size": page_size_int,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
            },
        )

    # -----------------------------------------
    # Full page load
    # -----------------------------------------
    log_action(
        current_user,
        "device_view",
        f"Device View - Full page load (page={page}, size={page_size_int}, sort={sort_by}:{sort_dir})",
        request,
        category="device",
    )

    return templates.TemplateResponse(
        "devices.html",
        {
            "request": request,
            "current_user": current_user,
            "devices": devices,
            "total": total,
            "page": page,
            "page_size": page_size_int,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
        },
    )

# Devices without pagination
# @router.get("/devices", response_class=HTMLResponse)
# async def devices_page(
#     request: Request,
#     current_user: Account | None = Depends(get_current_user_optional),
# ):
#     if current_user is None:
#         log_action(
#             current_user,
#             "device_view",
#             f"Device View - View Device page - Unauthorized",
#             request,
#             category="device",
#         )  
#         return RedirectResponse("/ui/login")

#     # Load devices from backend API
#     api_url = f"{settings.backend_url}/api/devices"

#     # Forward user cookies to API
#     cookies = request.cookies

#     api_resp = await request.app.state.http_client.get(
#         api_url,
#         cookies=cookies
#     )

#     # Parse JSON safely
#     try:
#         data = api_resp.json()

#     except Exception:
#         log_action(
#             current_user,
#             "device_view",
#             "Device View - Backend returned invalid JSON",
#             request,
#             category="device",
#         )
#         return templates.TemplateResponse(
#             "devices.html",
#             {
#                 "request": request,
#                 "current_user": current_user,
#                 "devices": [],
#                 "error": "Backend returned invalid JSON"
#             },
#             status_code=500,
#         )

#     # Handle backend error response
#     if not data.get("ok"):
#         log_action(
#             current_user,
#             "device_view",
#             f"Device View - Backend error: {data.get('error')}",
#             request,
#             category="device",
#         )
#         return templates.TemplateResponse(
#             "devices.html",
#             {
#                 "request": request,
#                 "current_user": current_user,
#                 "devices": [],
#                 "error": data.get("error"),
#             },
#             status_code=500,
#         )

#     # Extract device list
#     devices = data.get("devices", [])
#     has_approver = data.get("has_approver")

#     # Log success
#     log_action(
#         current_user,
#         "device_view",
#         "Device View - Logged-in user view device page",
#         request,
#         category="device",
#     )
#     # print(f"Request: {devices.request}")

#     # Render template
#     return templates.TemplateResponse(
#         "devices.html",
#         {
#             "request": request,
#             "current_user": current_user,
#             "devices": devices,
#             "has_approver": has_approver,
#         },
#     )

