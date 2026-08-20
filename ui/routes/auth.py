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

# ========================== Separated ==========================
@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, 
    current_user: Account | None = Depends(get_current_user_optional),):
    log_action(
        current_user,
        "page_view",
        "Viewed login page",
        request,
        category="navigation",
    )

    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login_submit(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    username: str | None = Form(None),
    password: str | None = Form(None),
):


    # Both missing
    if not username or not password:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Username and password are required"
            },
            status_code=400,
        )

    log_action(
        current_user,
        "login_attempt",
        f"Login attempt for username: {username}",
        request,
        category="authentication",
    )

    backend_login_url = f"{settings.backend_url}/api/login"

    # Call backend API
    api_resp = await request.app.state.http_client.post(
        backend_login_url,
        json={"username": username, "password": password}
    )

    # -----------------------------
    # Handle LOCKOUT (429)
    # -----------------------------
    if api_resp.status_code == 429:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Too many failed attempts. Your account is locked for 10 minutes."
            },
            status_code=429,
        )

    # -----------------------------
    # Handle invalid login (401)
    # -----------------------------
    if api_resp.status_code != 200:
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Invalid username or password"
            },
            status_code=401,
        )

    # -----------------------------
    # Successful login
    # -----------------------------
    data = api_resp.json()
    token = data["access_token"]

    redirect_url = "/ui/devices"
    response = RedirectResponse(url=redirect_url, status_code=302)

    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=False,  # True in production
        samesite="lax",
        path="/",
    )

    return response



@router.get("/logout")
async def logout(
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional)
):
    # Log BEFORE clearing the session
    if current_user:
        log_action(
            current_user,
            "logout",
            f"User {current_user.username} logged out",
            request,
            category="authentication",
        )
    else:
        log_action(
            None,
            "logout",
            "Anonymous user attempted logout",
            request,
            category="authentication",
        )

    # Call backend logout to delete DB session
    cookies = {"session": request.cookies.get("session")}
    backend_logout_url = f"{settings.backend_url}/api/logout"
    await request.app.state.http_client.post(
        backend_logout_url,
        cookies=cookies
    )

    # Clear cookie in browser
    response = RedirectResponse("/ui/login")
    response.delete_cookie("session")
    return response



# @router.get("/ui/restore-admin")
# async def restore_admin(db: AsyncSession = Depends(get_db)):
#     stmt = select(User)
#     result = await db.execute(stmt)
#     users = result.scalars().all()

#     if not users:
#         return {"error": "No users exist"}

#     # Promote first user to admin
#     user = users[0]
#     user.role = "admin"
#     await db.commit()

#     return {"status": "Admin restored", "username": user.username}
