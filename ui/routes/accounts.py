from urllib import response

from fastapi import APIRouter, Request, Depends, Form, Response, Body
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from core.db import get_db
from deps.auth import get_current_user_optional, get_current_user
from core.security import verify_password, hash_password
from models.account import Account
from core.audit_logger import log_action
from core.settings import settings

router = APIRouter(prefix="/ui", tags=["ui"])
templates = Jinja2Templates(directory="ui/templates")
templates.env.cache.clear()

async def count_admins(db: AsyncSession) -> int:
    stmt = select(Account).where(Account.role == "admin")
    result = await db.execute(stmt)
    return len(result.scalars().all())


# =============================== Separated =========================
# Admin Account Endpoints

# ---------------------------------------------------------
# GET ACCOUNTs TO EDIT
# ---------------------------------------------------------
@router.get("/accounts/{user_id}/edit")
async def admin_edit_page(
    user_id: int,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    # Auth check
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user,
            "accounts_edit_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="ui/accounts"
        )
        return RedirectResponse("/ui/login")

    # Choose correct backend endpoint
    api_url = f"{settings.backend_url}/api/accounts/{user_id}"

    # Forward session cookie to backend
    cookies = request.cookies

    print(f"url {api_url}")
    # Call backend API
    api_resp = await request.app.state.http_client.get(api_url, cookies=cookies)


    if api_resp.status_code == 404:
        return RedirectResponse("/ui/accounts")

    if api_resp.status_code != 200:
        return RedirectResponse("/ui/login")

    account = api_resp.json()

    log_action(
        current_user,
        "accounts_edit_view",
        f"Viewed account edit page for {account['username']}",
        request,
        category="ui/accounts"
    )

    return templates.TemplateResponse(
        "account_edit.html",
        {
            "request": request,
            "user": account,
            "current_user": current_user,
            "error": None,
        },
    )

# ---------------------------------------------------------
# EDIT ACCOUNTS SUBMISSION 
# ---------------------------------------------------------
@router.post("/accounts/{user_id}/edit", response_class=HTMLResponse)
async def accounts_edit_submit_page(
    user_id: int,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None:
        return RedirectResponse("/ui/login")

    # Extract form fields from frontend POST
    form = await request.form()

    payload = {
        "username": form.get("username"),
        "first_name": form.get("first_name"),
        "last_name": form.get("last_name"),
        "email": form.get("email"),
        "role": form.get("role"),
        "tenant": form.get("tenant"),
        "profiles": [p.strip() for p in form.get("profiles").split(",")] if form.get("profiles") else [],
        "new_password": form.get("new_password"),
        "confirm_password": form.get("confirm_password"),
    }

    # Backend API endpoint
    api_url = f"{settings.backend_url}/api/accounts/{user_id}"

    # Forward cookies (auth)
    cookies = request.cookies

    # Forward POST to backend API
    api_resp = await request.app.state.http_client.post(
        api_url,
        json=payload,
        cookies=cookies
    )

    data = api_resp.json()

    # If backend API returns error → re-render page with error
    if api_resp.status_code != 200:
        # Fetch latest user data again for re-render
        api_url = f"{settings.backend_url}/api/accounts/{user_id}"
        api_user_resp = await request.app.state.http_client.get(api_url, cookies=cookies)
        user_data = api_user_resp.json()

        return templates.TemplateResponse(
            "account_edit.html",
            {
                "request": request,
                "current_user": current_user,
                "user": user_data,
                "error": data.get("detail") or data.get("error") or "Unknown error",
            },
        )

    # Success → redirect to accounts page
    return RedirectResponse("/ui/accounts?success=1", status_code=302)

# ---------------------------------------------------------
# CREATE ACCOUNTS SUBMISSION
# ---------------------------------------------------------
@router.post("/accounts/create", response_class=HTMLResponse)
async def accounts_create_submit_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None:
        return RedirectResponse("/ui/login")

    # Extract form fields
    form = await request.form()

    # Convert profiles string → list
    profiles_raw = form.get("profiles")
    profiles_list = [p.strip() for p in profiles_raw.split(",") if p.strip()] if profiles_raw else []
    # Payload for backend API
    payload = {
        "username": form.get("username"),
        "password": form.get("password"),
        "confirm_password": form.get("confirm_password"),
        "role": form.get("role"),
        "tenant": form.get("tenant"),
        "first_name": form.get("first_name"),
        "last_name": form.get("last_name"),
        "email": form.get("email"),
        "source": form.get("source"),
        "profiles": profiles_list,
    }
    print(f"Payload: {payload}")
    # Backend API endpoint
    api_url = f"{settings.backend_url}/api/accounts"

    # Forward cookies for authentication
    cookies = request.cookies

    # Forward POST to backend API
    api_resp = await request.app.state.http_client.post(
        api_url,
        json=payload,
        cookies=cookies
    )

    data = api_resp.json()
    
    config = request.app.state.config
    tenants = list(config.get("tenants", {}).keys())
    # Handle backend errors
    if api_resp.status_code != 200:
        return templates.TemplateResponse(
            "account_create.html",
            {
                "request": request,
                "current_user": current_user,
                "error": data.get("detail") or data.get("error") or "Unknown error",
                "tenants": tenants,
            },
        )

    # Success → redirect to accounts list
    return RedirectResponse("/ui/accounts?success=1", status_code=303)

# ---------------------------------------------------------
# LIST ACCOUNTS
# ---------------------------------------------------------
@router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    # Authentication check
    if current_user is None:
        log_action(
            current_user,
            "accounts_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    # Authorization check
    if current_user.role != "admin":
        log_action(
            current_user,
            "accounts_view",
            "Attempted to view accounts list without admin privileges",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/devices")

    # Load backend URL from settings
    api_url = f"{settings.backend_url}/api/accounts"

    # Forward session cookie to backend
    cookies = request.cookies

    # Call backend API
    api_resp = await request.app.state.http_client.get(api_url, cookies=cookies)

    # Handle expired token or backend errors
    if api_resp.status_code == 401:
        return RedirectResponse("/ui/login")

    accounts = api_resp.json()

    log_action(
        current_user,
        "accounts_view",
        "Viewed accounts list",
        request,
        category="accounts"
    )

    return templates.TemplateResponse(
        "accounts.html",
        {
            "request": request,
            "current_user": current_user,
            "accounts": accounts,
        },
    )

# ---------------------------------------------------------
# CREATE ACCOUNTS
# ---------------------------------------------------------
@router.get("/accounts/create")
async def account_create_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user,
            "accounts_create_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    config = request.app.state.config
    tenants = list(config.get("tenants", {}).keys())
    
    log_action(
        current_user,
        "accounts_create_view",
        "Viewed account creation page",
        request,
        category="accounts"
    )

    return templates.TemplateResponse(
        "account_create.html",
        {
            "request": request,
            "current_user": current_user,
            "tenants": tenants,
            "error": None,
        },
    )

# ---------------------------------------------------------
# DELETE ACCOUNTS SUBMISSION
# ---------------------------------------------------------
@router.post("/accounts/{user_id}/delete")
async def accounts_delete(
    user_id: int,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    # Auth check
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user,
            "accounts_delete",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    # Backend URL
    api_url = f"{settings.backend_url}/api/accounts/{user_id}"

    # Forward session cookie
    cookies = request.cookies

    # Call backend DELETE
    api_resp = await request.app.state.http_client.delete(api_url, cookies=cookies)

    # Handle backend errors
    if api_resp.status_code == 400:
        # Last admin deletion attempt
        error_html = """
        <tr class='bg-red-100'>
            <td colspan='3' class='px-4 py-2 text-red-700 font-semibold'>
                Cannot delete the last admin account.
            </td>
        </tr>
        """

        # Reload accounts list from backend
        list_url = f"{settings.backend_url}/api/accounts"
        list_resp = await request.app.state.http_client.get(list_url, cookies=cookies)
        accounts = list_resp.json()

        table_html = templates.get_template("partials/accounts_table.html").render(
            request=request,
            accounts=accounts
        )

        return HTMLResponse(error_html + table_html)

    if api_resp.status_code != 200:
        return RedirectResponse("/ui/login")

    # Success — backend returned updated list
    accounts = api_resp.json()

    log_action(
        current_user,
        "accounts_delete",
        f"Account {user_id} deleted",
        request,
        category="accounts"
    )

    return templates.TemplateResponse(
        "partials/accounts_table.html",
        {"request": request, "accounts": accounts},
    )

# ---------------------------------------------------------
# DELETE ACCOUNTS INLINE CONFIRMATION
# ---------------------------------------------------------
@router.get("/accounts/{user_id}/delete-inline")
async def account_delete_inline(
    user_id: int,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    # Auth check
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user,
            "accounts_delete_inline",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    # Load backend URL
    api_url = f"{settings.backend_url}/api/accounts/{user_id}"

    # Forward session cookie
    cookies = request.cookies

    # Fetch account from backend
    api_resp = await request.app.state.http_client.get(api_url, cookies=cookies)

    if api_resp.status_code == 404:
        return HTMLResponse("<tr><td colspan='3'>User not found</td></tr>")

    account = api_resp.json()

    log_action(
        current_user,
        "accounts_delete_inline",
        f"Initiated inline delete confirmation for user {account['username']}",
        request,
        category="accounts"
    )

    # Inline confirmation HTML
    html = f"""
    <tr id="row-{account['id']}" class="bg-red-50">
      <td class="px-4 py-2" colspan="3">
        <div class="flex justify-between items-center">
          <span>Delete <strong>{account['username']}</strong>?</span>

          <div class="space-x-3">
           <button
            class="px-3 py-1 bg-gray-300 rounded"
            onclick="window.location.reload()">
            Cancel
            </button>

            <button
              class="px-3 py-1 bg-red-600 text-white rounded"
              hx-post="/ui/accounts/{account['id']}/delete"
              hx-target="#accounts-table"
              hx-swap="innerHTML">
              Confirm
            </button>
          </div>
        </div>
      </td>
    </tr>
    """

    return HTMLResponse(html)

# ---------------------------------------------------------
# GET ACCOUNTS PROFILE (Admin)
# ---------------------------------------------------------
@router.get("/accounts/{user_id}", response_class=HTMLResponse)
async def account_self(
    user_id: int,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    # Must be logged in
    if current_user is None:
        log_action(
            current_user,
            "accounts_self_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    # Normal users can ONLY view themselves
    if current_user.role != "admin" and current_user.id != user_id:
        log_action(
            current_user,
            "accounts_self_view",
            "Attempted to view another user's account",
            request,
            category="accounts"
        )
        return RedirectResponse(f"/ui/accounts/{current_user.id}")

    # Choose correct backend endpoint
    if current_user.role == "admin":
        api_url = f"{settings.backend_url}/api/accounts/{user_id}"
    else:
        api_url = f"{settings.backend_url}/api/accounts/me"

    cookies = request.cookies

    # Fetch account from backend API
    api_resp = await request.app.state.http_client.get(api_url, cookies=cookies)

    if api_resp.status_code != 200:
        return RedirectResponse("/ui/login")

    account = api_resp.json()

    success = request.query_params.get("success")

    log_action(
        current_user,
        "accounts_self_view",
        f"Viewed account page for user {account['username']}",
        request,
        category="accounts"
    )

    return templates.TemplateResponse(
        "account_self.html",
        {
            "request": request,
            "current_user": current_user,
            "user": account,
            "success": "Account updated successfully" if success else None
        },
    )


# SELF ACCOUNT ENDPOINT

# ---------------------------------------------------------
# GET SELF ACCOUNTS TO EDIT
# ---------------------------------------------------------
@router.get("/accounts/self/edit")
async def account_self_edit_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None:
        log_action(
            current_user,
            "accounts_self_edit_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    # Backend URL
    api_url = f"{settings.backend_url}/api/accounts/me"

    cookies = request.cookies

    # Fetch latest account data from backend
    api_resp = await request.app.state.http_client.get(api_url, cookies=cookies)

    if api_resp.status_code != 200:
        return RedirectResponse("/ui/login")

    account = api_resp.json()

    log_action(
        current_user,
        "accounts_self_edit_view",
        "Viewed self-edit page",
        request,
        category="accounts"
    )

    return templates.TemplateResponse(
        "account_self_edit.html",
        {
            "request": request,
            "current_user": current_user,
            "user": account,
            "error": None,
        },
    )

# ---------------------------------------------------------
# SELF ACCOUNTS EDIT SUBMISSION
# ---------------------------------------------------------
@router.post("/account/me/edit")
async def account_self_edit(
    request: Request,
    username: str = Form(None),
    email: str = Form(None),
    first_name: str = Form(None),
    last_name: str = Form(None),
    current_password: str = Form(None),
    new_password: str = Form(None),
    confirm_password: str = Form(None),
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None:
        log_action(
            current_user,
            "accounts_self_edit_submit",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    # Backend URL (new rule)
    api_url = f"{settings.backend_url}/api/account/{current_user.id}"

    payload = {
        "username": username,
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "current_password": current_password,
        "new_password": new_password,
        "confirm_password": confirm_password,
    }


    cookies = request.cookies

    # Call backend API
    api_resp = await request.app.state.http_client.put(api_url, json=payload, cookies=cookies)

    # Handle backend validation errors
    if api_resp.status_code != 200:
        try:
            data = api_resp.json()
            error = data.get("detail", "Unknown error")
        except Exception:
            error = api_resp.text or "Unknown error"


        log_action(
            current_user,
            "accounts_self_edit_submit",
            f"Self-edit failed: {error}",
            request,
            category="accounts"
        )

        return templates.TemplateResponse(
            "account_self_edit.html",
            {
                "request": request,
                "current_user": current_user,
                "user": current_user,
                "error": error,
            },
            status_code=400
        )

    # Success
    log_action(
        current_user,
        "accounts_self_edit_submit",
        "Submitted self-edit form",
        request,
        category="accounts"
    )

    return RedirectResponse(f"/ui/accounts/{current_user.id}?success=1", status_code=302)

@router.get("/account/me/edit")
async def account_self_edit_page(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
):
    if current_user is None:
        log_action(
            current_user,
            "accounts_self_edit_view",
            "Redirected to login page due to unauthenticated access attempt",
            request,
            category="accounts"
        )
        return RedirectResponse("/ui/login")

    log_action(
        current_user,
        "accounts_self_edit_view",
        "Viewed self-edit page",
        request,
        category="accounts"
    )

    return templates.TemplateResponse(
        "account_self_edit.html",
        {
            "request": request,
            "current_user": current_user, 
            "user": current_user,         
            "error": None,
        },
    )

# OTP Generate
@router.get("/account/me/otp", response_class=HTMLResponse)
async def ui_account_me_otp(request: Request, current_user = Depends(get_current_user_optional)):
    api_url = f"{settings.backend_url}/api/otp_qr"
    # print(f"Current user: {current_user.username}")
    api_resp = await request.app.state.http_client.get(
        api_url,
        params={"username": current_user.username},
        cookies=request.cookies
    )
    
    # Permission denied (backend RBAC)
    if api_resp.status_code == 403:
        log_action(
            current_user,
            "generate_otp",
            f"Permission denied when generating OTP for username={current_user.username}",
            request,
            category="otp"
        )
        return HTMLResponse(
            "<p class='text-red-600'>Permission denied: cannot generate OTP.</p>",
            status_code=403
        )

    # Backend error (anything except 200 or 403)
    if api_resp.status_code != 200:
        log_action(
            current_user,
            "generate_otp",
            f"Backend error during OTP generation for username={current_user.username}",
            request,
            category="otp"
        )
        return HTMLResponse(
            "<p class='text-red-600'>Failed to generate OTP.</p>",
            status_code=500
        )

    # Success
    data = api_resp.json()

    log_action(
        current_user,
        "generate_otp",
        f"OTP generated successfully for username={current_user.username}",
        request,
        category="otp"
    )

    # return f"""
    # <img src="data:image/png;base64,{data['qr_base64']}"
    #      class="border rounded p-2 bg-white"
    #      alt="OTP QR Code">
    # """
  
    qr = data["qr_base64"]
#     <div id="otp-wrapper" class="mt-4 p-2">
    # Return HTML fragment
    html = f"""
        <img src="data:image/png;base64,{qr}" class="border rounded p-2 bg-white">

        <div id="otp-wrapper" class="mt-4">

            <label class="block text-sm mb-1">Enter the 6‑digit code:</label>

            <input type="text"
                id="otp-code-input"
                name="code"
                class="border px-2 py-1 rounded w-32"
                maxlength="6"
                hx-post="/ui/account/me/otp_verify"
                hx-trigger="keyup[target.value.length == 6]"
                hx-target="#otp-verify-result"
                hx-swap="innerHTML"
                hx-include="#otp-code-input">

            <button class="bg-green-600 text-white px-3 py-1 rounded"
                    hx-post="/ui/account/me/otp_verify"
                    hx-target="#otp-verify-result"
                    hx-swap="innerHTML"
                    hx-include="#otp-code-input">
            Verify
            </button>

        </div>

        <div id="otp-verify-result" class="mt-3"></div>
        """

    return HTMLResponse(html)

# OTP Verify
@router.post("/account/me/otp_verify")
async def ui_otp_verify(request: Request, code: str = Form(...), current_user=Depends(get_current_user)):
    api = request.app.state.http_client
    form = await request.form()
    code = form.get("code")
    resp = await api.post(
        f"{settings.backend_url}/api/otp/verify",
        json={"code": code},
        cookies=request.cookies
    )

    data = resp.json()
    print(f"return data: {data}\n")

    if data["status"] == "ok":
        return HTMLResponse(
            "<p class='text-green-600 font-semibold'>OTP verified and saved.</p>"
        )

    return HTMLResponse(
        "<p class='text-red-600 font-semibold'>Invalid code. Try again.</p>"
        )
