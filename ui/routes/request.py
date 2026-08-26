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
from core.settings import settings
from core.permissions import has_permission
from core.debug import debug_error
from deps.auth import get_current_user
from datetime import datetime
router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.globals["has_permission"] = has_permission
templates.env.cache.clear()

# Request without sorting
# @router.get("/requests", response_class=HTMLResponse)
# async def requests_page(
#     request: Request,
#     current_user: Account = Depends(get_current_user_optional),
# ):
#     # ---------------------------------------------------------
#     # Session expired → redirect to login
#     # ---------------------------------------------------------
#     if current_user is None:
#         return RedirectResponse("/ui/login")

#     roles = request.app.state.roles

#     # ---------------------------------------------------------
#     # Frontend RBAC: must have read_requests permission
#     # ---------------------------------------------------------
#     if not has_permission(current_user.role, "read_requests", roles):
#         return templates.TemplateResponse(
#             "request.html",
#             {
#                 "request": request,
#                 "current_user": current_user,
#                 "requests": [],
#                 "error": "Permission denied",
#             },
#         )

#     # ---------------------------------------------------------
#     # Call backend API
#     # ---------------------------------------------------------
#     api_url = f"{settings.backend_url}/api/requests"

#     try:
#         api_resp = await request.app.state.http_client.get(
#             api_url,
#             cookies=request.cookies
#         )
#     except Exception as e:
#         return HTMLResponse(
#             f"""
#             <div class="p-4 bg-red-100 text-red-700 rounded">
#                 Frontend error contacting backend: {str(e)}
#             </div>
#             """
#         )

#     # ---------------------------------------------------------
#     # Parse backend JSON
#     # ---------------------------------------------------------
#     try:
#         data = api_resp.json()
#     except Exception:
#         data = {"ok": False, "error": "Invalid backend response"}

#     # ---------------------------------------------------------
#     # Backend permission or other error
#     # ---------------------------------------------------------
#     if not data.get("ok", False):
#         return templates.TemplateResponse(
#             "request.html",
#             {
#                 "request": request,
#                 "current_user": current_user,
#                 "requests": [],
#                 "error": data.get("error", "Unknown backend error"),
#             },
#         )

#     # ---------------------------------------------------------
#     # Success → render requests page
#     # ---------------------------------------------------------
#     return templates.TemplateResponse(
#         "request.html",
#         {
#             "request": request,
#             "current_user": current_user,
#             "requests": data.get("requests", []),
#         },
#     )

@router.get("/requests", response_class=HTMLResponse)
async def requests_page(
    request: Request,
    current_user: Account = Depends(get_current_user_optional),
    page: int = 1,
    page_size: str = "20",
    sort_by: str = "created_at",
    sort_dir: str = "desc",
):
    if current_user is None:
        return RedirectResponse("/ui/login")

    roles = request.app.state.roles

    if not has_permission(current_user.role, "read_requests", roles):
        template = "requests_table.html" if request.headers.get("HX-Request") else "request.html"
        return templates.TemplateResponse(
            template,
            {
                "request": request,
                "current_user": current_user,
                "requests": [],
                "error": "Permission denied",
                "page": page,
                "page_size": page_size,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "total": 0,
            },
        )
    try:
        if page_size == "custom":
            page_size_int = 20   # fallback default
        else:
            page_size_int = int(page_size)
    except ValueError:
        page_size_int = 20

    api_url = (
        f"{settings.backend_url}/api/requests"
        f"?page={page}&page_size={page_size_int}"
        f"&sort_by={sort_by}&sort_dir={sort_dir}"
    )

    api_resp = await request.app.state.http_client.get(api_url, cookies=request.cookies)
    data = api_resp.json()

    template = "partials/requests_table.html" if request.headers.get("HX-Request") else "request.html"

    return templates.TemplateResponse(
        template,
        {
            "request": request,
            "current_user": current_user,
            "requests": data.get("requests", []),
            "total": data.get("total", 0),
            "page": page,
            "page_size": page_size_int,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
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

    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Permission check (frontend RBAC)
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return templates.TemplateResponse(
            "partials/create_request.html",
            {
                "request": request,
                "device_name": device_name,
                "account_username": account_username,
                "error": "You do not have permission to create requests.",
            },
        )

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

    # ---------------------------------------------------------
    # Backend always returns JSON: { ok: True/False, error: "...", request_id: ... }
    # ---------------------------------------------------------
    try:
        data = resp.json()
    except Exception:
        data = {"ok": False, "error": "Invalid backend response"}

    # ---------------------------------------------------------
    # Backend error (ok=False)
    # ---------------------------------------------------------
    if not data.get("ok", False):
        error_msg = data.get("error", "Unknown backend error")

        return templates.TemplateResponse(
            "partials/create_request.html",
            {
                "request": request,
                "device_name": device_name,
                "account_username": account_username,
                "error": error_msg,
            },
        )

    # ---------------------------------------------------------
    # Success → HTMX redirect
    # ---------------------------------------------------------
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
    # ---------------------------------------------------------
    # Session expired → redirect to login
    # ---------------------------------------------------------
    if not current_user:
        return RedirectResponse("/ui/login", status_code=302)

    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Frontend RBAC check (same as backend)
    # ---------------------------------------------------------
    can_approve = has_permission(current_user.role, "approve_bg_account", roles)
    can_requester_otp = has_permission(current_user.role, "approve_bg_account_otp", roles)

    if not (can_approve or can_requester_otp):
        return templates.TemplateResponse(
            "partials/approve_request.html",
            {
                "request": request,
                "req": {"id": req_id},
                "error": "Permission denied",
            },
        )

    # ---------------------------------------------------------
    # Parse form fields from HTMX modal
    # ---------------------------------------------------------
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
        "approver_name": approver_name,
    }

    # ---------------------------------------------------------
    # Call backend API
    # ---------------------------------------------------------
    try:
        resp = await request.app.state.http_client.post(
            backend_url,
            json=payload,
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

    # ---------------------------------------------------------
    # Session expired or forbidden
    # ---------------------------------------------------------
    if resp.status_code in (401, 403):
        return RedirectResponse("/ui/login", status_code=302)

    # ---------------------------------------------------------
    # Parse backend JSON
    # ---------------------------------------------------------
    try:
        data = resp.json()
    except Exception:
        data = {"ok": False, "error": "Invalid backend response"}

    # ---------------------------------------------------------
    # ERROR CASE → backend returned ok=False
    # ---------------------------------------------------------
    if not data.get("ok", False):
        error_msg = data.get("error", "Unknown backend error")

        # OTP modal error
        if otp_code:
            # Re-fetch approver list
            approver_resp = await request.app.state.http_client.get(
                f"{settings.backend_url}/api/approverlist",
                cookies=request.cookies,
            )
            approver_data = approver_resp.json()
            approver_list = approver_data.get("approver_list", [])

            return templates.TemplateResponse(
                "partials/otp_approval.html",
                {
                    "request": request,
                    "req": {"id": req_id},
                    "error": error_msg,
                    "approver_list": approver_list,
                },
            )

        # Direct approval modal error
        return templates.TemplateResponse(
            "partials/approve_request.html",
            {
                "request": request,
                "req": {"id": req_id},
                "error": error_msg,
            },
        )

    # ---------------------------------------------------------
    # SUCCESS → HTMX redirect
    # ---------------------------------------------------------
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
    # ---------------------------------------------------------
    # Session expired → redirect to login
    # ---------------------------------------------------------
    if current_user is None:
        return RedirectResponse("/ui/login")

    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Frontend RBAC: must have request_bg_account permission
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 rounded'>Permission denied</div>"
        )

    backend_url = f"{settings.backend_url}/api/requests/{req_id}/show-password"

    # ---------------------------------------------------------
    # Call backend API
    # ---------------------------------------------------------
    try:
        resp = await request.app.state.http_client.get(
            backend_url,
            cookies=request.cookies,
        )
    except Exception as e:
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700 rounded'>Backend error: {e}</div>"
        )

    # ---------------------------------------------------------
    # Parse backend JSON
    # ---------------------------------------------------------
    try:
        data = resp.json()
    except Exception:
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 rounded'>Invalid backend response</div>"
        )

    # ---------------------------------------------------------
    # Backend permission or other error
    # ---------------------------------------------------------
    if not data.get("ok", False):
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700 rounded'>Error: {data.get('error')}</div>"
        )

    # ---------------------------------------------------------
    # Success → render password modal
    # ---------------------------------------------------------
    return templates.TemplateResponse(
        "partials/show_password.html",
        {
            "request": request,
            "password": data["password"],
            "device": data["device"],
            "username": data["username"],
            "req_id": req_id,
            "status": data.get("status"),
            "used_at": data.get("used_at"),
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
    # ---------------------------------------------------------
    # Session expired → redirect to login
    # ---------------------------------------------------------
    if current_user is None:
        return RedirectResponse("/ui/login")

    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Frontend RBAC: must have request_bg_account permission
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 rounded'>Permission denied</div>"
        )

    backend_url = f"{settings.backend_url}/api/requests/{req_id}/copy-password"

    # ---------------------------------------------------------
    # Call backend API
    # ---------------------------------------------------------
    try:
        resp = await request.app.state.http_client.get(
            backend_url,
            cookies=request.cookies,
        )
    except Exception as e:
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700 rounded'>Backend error: {e}</div>"
        )

    # ---------------------------------------------------------
    # Parse backend JSON
    # ---------------------------------------------------------
    try:
        data = resp.json()
    except Exception:
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 rounded'>Invalid backend response</div>"
        )

    # ---------------------------------------------------------
    # Backend permission or other error
    # ---------------------------------------------------------
    if not data.get("ok", False):
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700 rounded'>Error: {data.get('error')}</div>"
        )

    # ---------------------------------------------------------
    # Success → return JSON for HTMX clipboard copy
    # ---------------------------------------------------------
    return JSONResponse({
        "ok": True,
        "password": data["password"],
        "device": data.get("device"),
        "username": data.get("username"),
        "status": data.get("status"),
        "used_at": data.get("used_at"),
    })

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


@router.get("/requests/{req_id}/email-approve", response_class=HTMLResponse)
async def ui_email_approve(
    req_id: int,
    token: str,
    request: Request,
):
    backend_url = f"{settings.backend_url}/api/requests/{req_id}/email-approve"

    resp = await request.app.state.http_client.get(
        backend_url,
        params={"token": token},
    )

    data = resp.json()

    if data.get("ok"):
        message = f"Request #{req_id} has been successfully approved via email."
        color = "green"
    else:
        message = f"Email approval failed: {data.get('error', 'Unknown error')}"
        color = "red"

    return HTMLResponse(
        f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>Email Approval</title>
            <meta http-equiv="refresh" content="30;url=/ui/login">
            <link rel="stylesheet" href="/static/css/tailwind.min.css" />
        </head>
        <body class="bg-gray-50 min-h-screen flex items-center justify-center">

            <div class="bg-white shadow-md rounded p-6 text-center max-w-md">
                <h1 class="text-2xl font-bold text-{color}-600 mb-4">
                    {message}
                </h1>

                <p class="text-gray-600">
                    You will be redirected to the login page in 30 seconds.
                </p>

                <p class="mt-4">
                    <a href="/ui/login"
                       class="text-blue-600 underline">
                       Click here if you are not redirected
                    </a>
                </p>
            </div>

        </body>
        </html>
        """
    )


@router.post("/requests/{req_id}/close", response_class=HTMLResponse)
async def ui_close_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
):
    # ---------------------------------------------------------
    # Session check
    # ---------------------------------------------------------
    if current_user is None:
        return RedirectResponse("/ui/login")

    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Permission check
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 rounded'>Permission denied</div>"
        )

    backend_url = f"{settings.backend_url}/api/requests/{req_id}/close"

    # ---------------------------------------------------------
    # Call backend
    # ---------------------------------------------------------
    try:
        resp = await request.app.state.http_client.post(
            backend_url,
            cookies=request.cookies,
        )
    except Exception as e:
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700 rounded'>Backend error: {e}</div>"
        )

    # ---------------------------------------------------------
    # Parse JSON
    # ---------------------------------------------------------
    try:
        data = resp.json()
    except Exception:
        return HTMLResponse(
            "<div class='p-4 bg-red-100 text-red-700 rounded'>Invalid backend response</div>"
        )

    # ---------------------------------------------------------
    # Error from backend
    # ---------------------------------------------------------
    if not data.get("ok", False):
        return HTMLResponse(
            f"<div class='p-4 bg-red-100 text-red-700 rounded'>Error: {data.get('error')}</div>"
        )

    # ---------------------------------------------------------
    # Success
    # ---------------------------------------------------------
    return HTMLResponse(
    """
    <div 
        class="bg-green-600 text-white px-4 py-2 rounded shadow-lg text-center animate-fade-in"
        hx-trigger="load"
        hx-on="htmx:afterSwap: setTimeout(() => { 
            document.getElementById('toast-container').innerHTML = ''; 
            window.location.reload(); 
        }, 2000)"
    >
        Request closed. Rotation queued.
    </div>
    """
    )

    # return HTMLResponse(
    #     f"""
    #     <div class='p-4 bg-green-100 text-green-800 rounded'>
    #         Request closed. Rotation queued.
    #     </div>
    #     """
    # )
