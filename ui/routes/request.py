from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.vault import VaultClient

from core.db import get_db
from deps.auth import get_current_user_optional
from core.security import verify_password, create_access_token, hash_password
from models.account import Account
from models.request import BreakglassRequest
from core.device_loader import load_devices
from core.audit_logger import log_action
from core.settings import settings
from core.debug import debug_error
from deps.auth import get_current_user
from datetime import datetime
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


@router.post("/requests/create")
async def ui_create_request(
    request: Request,
    device_name: str = Form(...),
    account_username: str = Form(...),
    start_time: datetime = Form(...),
    end_time: datetime = Form(...),
    request_reason: str = Form(...),
    current_user: Account = Depends(get_current_user),
):
    """
    Frontend endpoint that proxies the backend API.
    Returns HTML fragments for HTMX modal or redirects on success.
    """

    backend_url = f"{settings.backend_url}/api/requests/create"

    # Forward cookies (auth)
    cookies = request.cookies

    try:
        resp = await request.app.state.http_client.post(
            backend_url,
            data={
                "device_name": device_name,
                "account_username": account_username,
                "start_time": start_time.isoformat(),
                "end_time": end_time.isoformat(),
                "request_reason": request_reason,
            },
            cookies=cookies,
        )

    except Exception as e:
        return HTMLResponse(
            f"""
            <div class="p-4 bg-red-100 text-red-700 rounded">
                Frontend error contacting backend: {str(e)}
            </div>
            """
        )

    # Backend returned error (e.g., 409 duplicate)
    if resp.status_code != 200:
        try:
            error_msg = resp.json().get("detail", "Unknown error")
        except Exception:
            error_msg = "Unknown backend error"

        return templates.TemplateResponse(
            "partials/create_request.html",
            {
                "request": request,
                "device_name": device_name,
                "account_username": account_username,
                "error": error_msg,
            },
        )

    # Success → HTMX redirect
    return Response(
        headers={"HX-Redirect": "/ui/devices"},
        status_code=200
    )
    # # Success → redirect to devices page
    # return RedirectResponse("/ui/devices", status_code=302)



@router.get("/requests/{req_id}/approve-modal", response_class=HTMLResponse)
async def approve_modal(req_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    return templates.TemplateResponse("partials/approve_request.html", {"request": request, "req": req})

@router.post("/requests/{req_id}/approve", response_class=HTMLResponse)
async def ui_approve_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    ):
    # If session expired → redirect to login
    if not current_user:
        return RedirectResponse("/ui/login", status_code=302)

    # Parse form fields from HTMX modal
    form = await request.form()
    approve_reason = form.get("approve_reason")
    approver_name = form.get("approver_name")
    otp_code = form.get("otp_code")

    # Backend API endpoint
    backend_url = f"{settings.backend_url}/api/requests/{req_id}/approve"

    # Forward cookies for authentication
    cookies = request.cookies

    # Prepare payload for backend API
    payload = {
        "approve_reason": approve_reason,
        "otp_code": otp_code,
        "approver_name": approver_name
    }

    # Call backend API
    resp = await request.app.state.http_client.post(
        backend_url,
        json=payload,
        cookies=cookies,
    )

    # Session expired or forbidden
    if resp.status_code in (401, 403):
        return RedirectResponse("/ui/login", status_code=302)

    data = resp.json()

    # ERROR CASE → return correct modal
    if "detail" in data:
        if otp_code:
            # Re-fetch approver list from backend
            approver_resp = await request.app.state.http_client.get(
                f"{settings.backend_url}/api/approverlist",
                cookies=request.cookies,
            )
            approver_data = approver_resp.json()
            approver_list = approver_data.get("approver_list", [])
            # Return OTP modal again
            return templates.TemplateResponse(
                "partials/otp_approval.html",
                {
                    "request": request,
                    "req": {"id": req_id},
                    "error": data["detail"],
                    "approver_list": approver_list,  # you can reload list if needed
                },
            )
        else:
            # Return direct approval modal
            return templates.TemplateResponse(
                "partials/approve_request.html",
                {
                    "request": request,
                    "req": {"id": req_id},
                    "error": data["detail"],
                },
            )

    # Success → HTMX redirect
    return Response(
        headers={"HX-Redirect": "/ui/requests"},
        status_code=200
    )



@router.get("/requests/{req_id}/otp-modal", response_class=HTMLResponse)
async def otp_modal(
    req_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    # Load the breakglass request
    req = await db.scalar(
        select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    )

    backend_url = f"{settings.backend_url}/api/approverlist"

    resp = await request.app.state.http_client.get(
        backend_url,
        cookies=request.cookies,   # forward session cookies
    )

    data = resp.json()
    approver_list = data.get("approver_list", [])

    return templates.TemplateResponse(
        "partials/otp_approval.html",
        {
            "request": request,
            "req": req,
            "approver_list": approver_list,
        }
    )




@router.get("/requests/{req_id}/reject-modal", response_class=HTMLResponse)
async def reject_modal(req_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    return templates.TemplateResponse("partials/reject_request.html", {"request": request, "req": req})

@router.post("/requests/{req_id}/reject", response_class=HTMLResponse)
async def ui_reject_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    ):
    # Session expired → redirect
    if not current_user:
        return Response(headers={"HX-Redirect": "/ui/login"}, status_code=200)

    # Parse form fields from HTMX modal
    form = await request.form()
    reject_reason = form.get("reject_reason")

    # Backend API endpoint
    backend_url = f"{settings.backend_url}/api/requests/{req_id}/reject"

    # Forward cookies for authentication
    cookies = request.cookies

    # Prepare payload for backend API
    payload = {
        "reject_reason": reject_reason,
    }

    # Call backend API
    resp = await request.app.state.http_client.post(
        backend_url,
        json=payload,
        cookies=cookies,
    )

    # Session expired or forbidden
    if resp.status_code in (401, 403):
        return Response(headers={"HX-Redirect": "/ui/login"}, status_code=200)

    data = resp.json()

    # Backend returned error → show error inside modal
    if "detail" in data:
        return templates.TemplateResponse(
            "partials/reject_request.html",
            {
                "request": request,
                "req": {"id": req_id},
                "error": data["detail"],
            },
        )

    return Response(
        headers={"HX-Redirect": "/ui/requests"},
        status_code=200
    )


@router.get("/requests/{req_id}/show-password", response_class=HTMLResponse)
async def ui_show_password(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
):
    backend_url = f"{settings.backend_url}/api/requests/{req_id}/show-password"

    try:
        resp = await request.app.state.http_client.get(
            backend_url,
            cookies=request.cookies,
        )
    except Exception as e:
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700'>Backend error: {e}</div>"
        )

    data = resp.json()

    if not data.get("ok"):
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700'>Error: {data.get('error')}</div>"
        )

    password = data["password"]

    return templates.TemplateResponse(
        "partials/show_password.html",
        {
            "request": request,
            "password": data["password"],
            "device": data["device"],
            "username": data["username"],
            "req_id": req_id,
        },
    )
    # return HTMLResponse(
    #     f"""
    #     <div hx-on:load="
    #         setTimeout(() => {{
    #             htmx.ajax('GET', '/ui/requests/{req_id}/show-password-hide', '#pwbox-{req_id}');
    #         }}, 20000);
    #     ">
    #         <span class="font-mono text-lg">{password}</span>
    #         <button
    #             class="ml-2 text-blue-600 hover:text-blue-800"
    #             hx-on:click="
    #                 navigator.clipboard.writeText('{password}');
    #                 htmx.ajax('GET', '/ui/requests/{req_id}/show-password-hide', '#pwbox-{req_id}');
    #             ">
    #             📋
    #         </button>

    #         <button
    #             class="ml-4 text-gray-700 underline"
    #             hx-get="/ui/requests/{req_id}/show-password-hide"
    #             hx-target="#pwbox-{req_id}"
    #             hx-swap="innerHTML">
    #             Hide Password
    #         </button>
    #     </div>
    #     """
    # )
 

@router.get("/requests/{req_id}/copy-password", response_class=HTMLResponse)
async def ui_copy_password(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
):
    backend_url = f"{settings.backend_url}/api/requests/{req_id}/copy-password"

    try:
        resp = await request.app.state.http_client.get(
            backend_url,
            cookies=request.cookies,
        )
    except Exception as e:
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700'>Backend error: {e}</div>"
        )

    data = resp.json()

    if not data.get("ok"):
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700'>Error: {data.get('error')}</div>"
        )

    password = data["password"]
    return JSONResponse({"ok": True, "password": password})
    # print(password)
    # return HTMLResponse(
    #     f"""
    #     <div
    #     id="toast-msg"
    #     hx-on:load="
    #         navigator.clipboard.writeText('{password}');
    #         showCopyToast('Password copied');
    #         setTimeout(() => document.getElementById('toast-msg').remove(), 10);
    #     "
    #     class="hidden">
    #     </div>
    #     """
    # )






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
