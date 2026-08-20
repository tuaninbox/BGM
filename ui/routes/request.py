from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.vault import VaultClient

from core.db import get_db
from deps.auth import get_current_user_optional
from core.security import verify_password, create_access_token, hash_password
from models.account import Account
from core.device_loader import load_devices
from core.audit_logger import log_action
from core.settings import settings
from core.debug import debug_error

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.cache.clear()

@router.get("/requests", response_class=HTMLResponse)
async def requests_page(
    request: Request,
    current_user: Account = Depends(get_current_user_optional),
):
    if current_user is None:
        return RedirectResponse("/ui/login")

    api_url = f"{settings.backend_url}/api/requests"
    api_resp = await request.app.state.http_client.get(api_url, cookies=request.cookies)
    data = api_resp.json()

    return templates.TemplateResponse(
        "request.html",
        {
            "request": request,
            "current_user": current_user,
            "requests": data.get("requests", []),
        },
    )

@router.get("/requests/create-modal/{device_name}", response_class=HTMLResponse)
async def create_request_modal(device_name: str, request: Request, db: AsyncSession = Depends(get_db)):
    try:
        vault = VaultClient(request.app.state.config, tenant="NCP")
        bgaccounts = await vault.get_breakglass_accounts()
        bg_lookup = {item["device"]: item for item in bgaccounts}

        bg = bg_lookup.get(device_name)
        account_username = bg["username"] if bg else None

        return templates.TemplateResponse(
            "partials/create_request.html",
            {
                "request": request,
                "device_name": device_name,
                "account_username": account_username,
            },
        )

    except Exception as e:
        if settings.debug:
            # Full stack trace
            print(f"create-modal: {e}")
            return HTMLResponse(debug_error(e), status_code=500)

        # Clean production error
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 border border-red-300 rounded'>"
            "An unexpected error occurred.</div>",
            status_code=500
        )



@router.get("/requests/{req_id}/approve-modal", response_class=HTMLResponse)
async def approve_modal(req_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    return request.app.state.templates.TemplateResponse("partials/approve_request.html", {"request": request, "req": req})


@router.get("/requests/{req_id}/otp-modal", response_class=HTMLResponse)
async def otp_modal(req_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    return request.app.state.templates.TemplateResponse("partials/otp_approve.html", {"request": request, "req": req})


@router.get("/requests/{req_id}/reject-modal", response_class=HTMLResponse)
async def reject_modal(req_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    return request.app.state.templates.TemplateResponse("partials/reject_request.html", {"request": request, "req": req})


# @router.get("/requests/{req_id}/approve-modal", response_class=HTMLResponse)
# async def approve_modal(
#     req_id: int,
#     request: Request,
#     db: AsyncSession = Depends(get_db),
# ):
#     stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
#     result = await db.execute(stmt)
#     req = result.scalar_one_or_none()

#     return request.app.state.templates.TemplateResponse(
#         "modals/approve_request.html",
#         {"request": request, "req": req},
#     )


# @router.get("/requests/{req_id}/reject-modal", response_class=HTMLResponse)
# async def reject_modal(
#     req_id: int,
#     request: Request,
#     db: AsyncSession = Depends(get_db),
# ):
#     stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
#     result = await db.execute(stmt)
#     req = result.scalar_one_or_none()

#     return request.app.state.templates.TemplateResponse(
#         "modals/reject_request.html",
#         {"request": request, "req": req},
#     )
