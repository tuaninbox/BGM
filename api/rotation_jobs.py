from fastapi import APIRouter, Request
from core.db import get_db
from sqlalchemy import select
from models.request import BreakglassRequest
from core.permissions import has_permission
from models.account import Account
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api", tags=["accounts"])

@router.get("/rotation/jobs")
async def api_rotation_jobs(
    request: Request,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user),
):
    roles = request.app.state.roles

    # Permission check
    if not has_permission(current_user.role, "read_background_jobs", roles):
        return {"ok": False, "error": "Permission denied"}

    stmt = select(BreakglassRequest).where(
        BreakglassRequest.rotation_status.isnot(None)
    )

    if status:
        stmt = stmt.where(BreakglassRequest.rotation_status == status)

    stmt = stmt.order_by(BreakglassRequest.rotation_at.desc())

    result = await db.execute(stmt)
    jobs = result.scalars().all()

    return {
        "ok": True,
        "jobs": [
            {
                "id": j.id,
                "device_name": j.device_name,
                "account_username": j.account_username,
                "rotation_status": j.rotation_status,
                "rotation_retries": j.rotation_retries,
                "rotation_at": j.rotation_at,
                "rotation_error": j.rotation_error,
            }
            for j in jobs
        ]
    }
