from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.db import get_db
from deps.auth import get_current_user_optional
from core.security import verify_password, create_access_token, hash_password
from models.account import Account
from core.device_loader import load_devices
from core.audit_logger import log_action
from core.settings import settings

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.cache.clear()

@router.get("/devices", response_class=HTMLResponse)
async def devices_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None:
        log_action(
            current_user,
            "device_view",
            f"Device View - View Device page - Unauthorized",
            request,
            category="device",
        )  
        return RedirectResponse("/ui/login")

    # Load devices from backend API
    api_url = f"{settings.backend_url}/api/devices"

    # Forward user cookies to API
    cookies = request.cookies

    api_resp = await request.app.state.http_client.get(
        api_url,
        cookies=cookies
    )

    # Parse JSON safely
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
                "error": "Backend returned invalid JSON"
            },
            status_code=500,
        )

    # Handle backend error response
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
            },
            status_code=500,
        )

    # Extract device list
    devices = data.get("devices", [])
    has_approver = data.get("has_approver")

    # Log success
    log_action(
        current_user,
        "device_view",
        "Device View - Logged-in user view device page",
        request,
        category="device",
    )

    # Render template
    return templates.TemplateResponse(
        "devices.html",
        {
            "request": request,
            "current_user": current_user,
            "devices": devices,
            "has_approver": has_approver,
        },
    )

@router.get("/interactive")
async def interactive_page(request: Request):
    return templates.TemplateResponse("interactive.html", {"request": request})
