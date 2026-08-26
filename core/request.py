from datetime import datetime, timezone
from sqlalchemy import select
from core.db import AsyncSessionLocal
from core.audit_logger import log_action
from models.request import BreakglassRequest
from core.utils import parse_iso8601


async def cleanup_requests():
    """
    Runs every minute.
    Expires or closes Breakglass requests whose end_time has passed.
    """

    now_dt = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(BreakglassRequest).where(
                BreakglassRequest.status.in_(["pending", "approved", "used", "closed"])
            )
        )
        rows = result.scalars().all()

        changed = False

        for r in rows:
            end_dt = parse_iso8601(r.end_time)
            if end_dt is None:
                continue

            if end_dt < now_dt:

                # pending → expired
                if r.status == "pending":
                    r.status = "expired"
                    changed = True

                # approved → expired (never used)
                elif r.status == "approved":
                    r.status = "expired"
                    changed = True

                # used → closed → queue rotation
                elif r.status == "used":
                    r.status = "closed"
                    r.rotation_status = "pending"
                    r.rotation_at = None
                    changed = True

        if changed:
            await db.commit()
            print("✔ Cleanup: expired/closed requests updated")
