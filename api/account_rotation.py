from fastapi import APIRouter, Request, Depends
from core.db import get_db
from sqlalchemy import select, func
from models.request import BreakglassRequest
from core.permissions import has_permission
from models.account import Account
from sqlalchemy.ext.asyncio import AsyncSession
from core.settings import settings
from deps.auth import get_current_user
from core.audit_logger import log_action

router = APIRouter(prefix="/api", tags=["accounts"])

# @router.get("/rotation/jobs")
# async def api_rotation_jobs(
#     request: Request,
#     status: str | None = None,
#     db: AsyncSession = Depends(get_db),
#     current_user: Account = Depends(get_current_user),
# ):

#     roles = request.app.state.roles

#     # Permission check
#     if not has_permission(current_user.role, "read_rotation_jobs", roles):
#         return {"ok": False, "error": "Permission denied"}

#     stmt = select(BreakglassRequest).where(
#         BreakglassRequest.rotation_status.isnot(None)
#     )

#     if status:
#         stmt = stmt.where(BreakglassRequest.rotation_status == status)

#     stmt = stmt.order_by(BreakglassRequest.rotation_at.desc())

#     result = await db.execute(stmt)
#     jobs = result.scalars().all()

#     return {
#         "ok": True,
#         "jobs": [
#             {
#                 "id": j.id,
#                 "device_name": j.device_name,
#                 "account_username": j.account_username,
#                 "rotation_status": j.rotation_status,
#                 "rotation_at": j.rotation_at,
#                 "rotation_error": j.rotation_error,
#             }
#             for j in jobs
#         ]
#     }

@router.get("/rotation/jobs")
async def api_rotation_jobs(
    request: Request,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "rotation_at",
    sort_dir: str = "desc",
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user),
):

    roles = request.app.state.roles

    # Permission check
    if not has_permission(current_user.role, "read_rotation_jobs", roles):
        return {"ok": False, "error": "Permission denied"}

    # Valid sort columns
    valid_sort_columns = {
        "id": BreakglassRequest.id,
        "device_name": BreakglassRequest.device_name,
        "account_username": BreakglassRequest.account_username,
        "rotation_status": BreakglassRequest.rotation_status,
        "rotation_at": BreakglassRequest.rotation_at,
        "created_at": BreakglassRequest.created_at,
        "approved_at": BreakglassRequest.approved_at,
        "used_at": BreakglassRequest.used_at,
    }

    sort_column = valid_sort_columns.get(sort_by, BreakglassRequest.rotation_at)
    order = sort_column.asc() if sort_dir == "asc" else sort_column.desc()

    # Base query
    stmt = select(BreakglassRequest).where(
        BreakglassRequest.rotation_status.isnot(None)
    )

    # Status filter
    if status and status != "":
        stmt = stmt.where(BreakglassRequest.rotation_status == status)

    # Count total
    count_stmt = stmt.with_only_columns(func.count()).order_by(None)
    total = (await db.execute(count_stmt)).scalar_one()

    # Pagination
    stmt = stmt.order_by(order)
    stmt = stmt.limit(page_size).offset((page - 1) * page_size)

    result = await db.execute(stmt)
    jobs = result.scalars().all()

    # Response
    return {
        "ok": True,
        "total": total,
        "status": status,
        "jobs": [
            {
                "id": j.id,
                "device_name": j.device_name,
                "account_username": j.account_username,
                "rotation_status": j.rotation_status,
                "rotation_at": j.rotation_at,
                "rotation_error": j.rotation_error,
                "rotation_first_error": j.rotation_first_error,
                "rotation_failure_notified": j.rotation_failure_notified,
            }
            for j in jobs
        ],
    }



@router.post("/simulate_rotation")
async def mock_rotate(payload: dict):
    req_id = payload["req_id"]
    import asyncio, httpx
    # Simulate rotation delay
    await asyncio.sleep(20)

    # Call back to your callback endpoint
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{settings.backend_url}/api/rotation-callback",
            json={
                "req_id": req_id,
                "status": "success",
                "error": None
            },
            headers={"Authorization": f"Bearer {settings.rotation_api_token}"}
        )
    log_action(
        user="system",
        action="rotation_callback",
        details=f"Rotation callback successfully for request {req_id}",
        request=None,
        category="simulation"
    )
    return {"ok": True, "message": "Rotation simulated"}
