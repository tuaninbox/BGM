from api import device, inventory
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from core.db import Base, engine, AsyncSessionLocal
from core.security import hash_password
# from core.middleware import AuditMiddleware
#from starlette.middleware.sessions import SessionMiddleware
from models.account import Account
from models.request import BreakglassRequest
from core.settings import settings
from datetime import datetime, timezone
from core.utils import parse_iso8601
from core.account_rotation import rotate_accounts_for_closed_requests
from core.email import cleanup_email_approval_tokens
from core.request import cleanup_requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.orm import sessionmaker

# API Routers
from api import auth, accounts, logs as api_logs, request, dashboard, account_rotation

# UI Routers
from ui.routes import (
    auth as ui_auth,
    accounts as ui_accounts,
    devices as ui_devices,
    logs as ui_logs,
    request as ui_requests,
    dashboard as ui_dashboard,
    account_rotation as ui_account_rotation
)

from core.config_loader import load_config
from core.credential_loader import load_credentials
from core.device_loader import load_devices
from core.role_loader import load_roles
from fastapi.responses import RedirectResponse
from fastapi.exceptions import RequestValidationError
from core.debug import validation_exception_handler



app = FastAPI(title="Breakglass Management")
# app.add_middleware(AuditMiddleware)
# Install SessionMiddleware using secret key from settings
#app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

app.mount("/static", StaticFiles(directory="ui/static"), name="static")

# ---------------------------------------------------------
# Seed admin user
# ---------------------------------------------------------
async def seed_admin_user():
    """
    Create an admin user on first startup if none exists.
    This runs automatically and is idempotent.
    """
    async with AsyncSessionLocal() as db:
        stmt = select(Account).where(Account.username == "admin")
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return  # Admin already exists

        admin_user = Account(
            username="admin",
            email="admin@example.com",
            role="admin",
            password_hash=hash_password("admin123"),
            source="local",
            first_name="Super",
            last_name="Admin",
            tenant="NCP"
        )

        db.add(admin_user)
        await db.commit()
        print("✔ Admin user created: username=admin password=admin123")

# async def cleanup_requests():
#     now_dt = datetime.now(timezone.utc)

#     SchedulerSession = sessionmaker(
#         engine,
#         expire_on_commit=False,
#         class_=AsyncSession
#     )

#     async with SchedulerSession() as db:
#         result = await db.execute(
#             select(BreakglassRequest).where(
#                 BreakglassRequest.status.in_(["pending", "approved", "used", "closed"])
#             )
#         )
#         rows = result.scalars().all()

#         changed = False

#         for r in rows:
#             end_dt = parse_iso8601(r.end_time)
#             if end_dt is None:
#                 continue

#             if end_dt < now_dt:

#                 # pending → expired
#                 if r.status == "pending":
#                     r.status = "expired"
#                     changed = True

#                 # approved → expired (never used)
#                 elif r.status == "approved":
#                     r.status = "expired"
#                     changed = True

#                 # used → closed → queue rotation
#                 elif r.status == "used":
#                     r.status = "closed"
#                     r.rotation_status = "pending"
#                     r.rotation_at = None
#                     changed = True

#                 # closed → queue rotation if not already queued
#                 elif r.status == "closed":
#                     if r.rotation_status == "not_required":
#                         r.rotation_status = "pending"
#                         r.rotation_at = None
#                         changed = True

#         if changed:
#             await db.commit()
#             print("✔ Cleanup: expired/closed requests updated")

def start_scheduler():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(cleanup_requests, "interval", minutes=1)
    scheduler.add_job(rotate_accounts_for_closed_requests, "interval", minutes=1)
    scheduler.add_job(cleanup_email_approval_tokens, "interval", minutes=5)
    # Weekly summary every Monday at 08:00
    # scheduler.add_job(send_weekly_rotation_summary,"cron",day_of_week="mon",hour=8,minute=0)
    # Monthly summary on the 1st at 08:00
    #scheduler.add_job(send_monthly_rotation_summary,"cron",day=1,hour=8,minute=0)
    scheduler.start()
    print("✔ APScheduler started")
# ---------------------------------------------------------
# Startup
# ---------------------------------------------------------
@app.on_event("startup")
async def startup():
    # Load global config (tenants, roles, etc.)
    app.state.config = load_config()
    # print("✔ Loaded global config: ",app.state.config)
    print("✔ Loaded global config")

    # Store loaders for later use
    app.state.credential_loader = load_credentials
    app.state.device_loader = load_devices
    
    # Load roles
    app.state.roles = load_roles()
    print("✔ Loaded roles")

    # Create async HTTP client for UI → API calls
    app.state.http_client = httpx.AsyncClient()
    print("✔ HTTP client initialized")

    # DO NOT LOAD DEVICES OR CREDENTIALS HERE
    # They depend on tenant, and tenant is unknown at startup.

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed admin user
    await seed_admin_user()

    # Create missing column
    # async with engine.begin() as conn:
    #     await conn.run_sync(Base.metadata.create_all)

    print("✔ Startup complete (tenant-specific loading deferred)")
    start_scheduler()


# ---------------------------------------------------------
# Shutdown
# ---------------------------------------------------------
@app.on_event("shutdown")
async def shutdown():
    # Close async HTTP client
    await app.state.http_client.aclose()
    print("✔ HTTP client closed")


# ---------------------------------------------------------
# Routers
# ---------------------------------------------------------

# API Routers
app.include_router(auth.router)
app.include_router(inventory.router)
app.include_router(accounts.router)
app.include_router(api_logs.router)
app.include_router(device.router)
app.include_router(request.router)
app.include_router(dashboard.router)
app.include_router(account_rotation.router)

# UI Routers
app.include_router(ui_auth.router)
app.include_router(ui_accounts.router)
app.include_router(ui_devices.router)
app.include_router(ui_logs.router)
app.include_router(ui_requests.router)
app.include_router(ui_dashboard.router)
app.include_router(ui_account_rotation.router)

app.add_exception_handler(RequestValidationError, validation_exception_handler)
# ---------------------------------------------------------
# Root redirect
# ---------------------------------------------------------
@app.get("/")
async def root():
    return RedirectResponse(url="/ui/login")

# Serve favicon.ico at root
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse("ui/static/favicon.ico")