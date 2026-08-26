import asyncio
from sqlalchemy import select
from datetime import datetime, timezone
import httpx

from core.db import AsyncSessionLocal
from models.request import BreakglassRequest
from core.settings import settings
from core.email import send_rotation_email
from core.audit_logger import log_action


async def rotate_accounts_for_closed_requests():
    async with AsyncSessionLocal() as db:
        stmt = select(BreakglassRequest).where(
            BreakglassRequest.status == "closed",
            BreakglassRequest.rotation_status.in_(["pending", None, "failed"]),
        )

        result = await db.execute(stmt)
        rows = result.scalars().all()

        if not rows:
            return

        async with httpx.AsyncClient(timeout=10) as client:
            for r in rows:

                # Exponential backoff retry loop
                for attempt in range(settings.rotation_max_retries):
                    try:
                        log_action(
                            user="system",
                            action="rotation_attempt",
                            details=f"Attempt {attempt+1}/{settings.rotation_max_retries} for request {r.id}",
                            request=None,
                            category="rotation"
                        )

                        # Mark queued
                        r.rotation_status = "queued"
                        r.rotation_at = datetime.now(timezone.utc)
                        await db.commit()

                        resp = await client.post(
                            f"{settings.rotation_api_url}/api/simulate_rotation",
                            json={
                                "req_id": r.id,
                                "device_name": r.device_name,
                                "account_username": r.account_username,
                            },
                            headers={"Authorization": f"Bearer {settings.rotation_api_token}"}
                        )

                        if resp.status_code == 200:
                            # Success
                            r.rotation_status = "running"
                            r.rotation_error = None
                            r.rotation_at = datetime.now(timezone.utc)
                            await db.commit()

                            log_action(
                                user="system",
                                action="rotation_start",
                                details=f"Rotation started for request {r.id}",
                                request=None,
                                category="rotation"
                            )
                            break  # stop retrying

                        else:
                            # Failure
                            r.rotation_status = "failed"
                            r.rotation_error = f"HTTP {resp.status_code}: {resp.text}"
                            r.rotation_at = datetime.now(timezone.utc)
                            await db.commit()

                            # Exponential backoff
                            await asyncio.sleep(2 ** attempt)

                    except Exception as e:
                        r.rotation_status = "failed"
                        r.rotation_error = str(e)
                        r.rotation_at = datetime.now(timezone.utc)
                        await db.commit()

                        # Exponential backoff
                        await asyncio.sleep(2 ** attempt)

                # After all retries
                if r.rotation_status == "failed":
                    # Set timestamp only on first failure
                    if not r.rotation_first_error:
                        r.rotation_first_error = datetime.now(timezone.utc).isoformat()
                        await db.commit()


                    now_dt = datetime.now(timezone.utc)

                    # rotation_first_error is stored as ISO string
                    first_error_dt = datetime.fromisoformat(r.rotation_first_error)

                    # compute elapsed time correctly
                    elapsed = now_dt - first_error_dt


                    if elapsed.total_seconds() >= settings.rotation_failure_window_minutes * 60:
                        if not r.rotation_failure_notified:
                            await send_rotation_email(r,success=False)
                            r.rotation_failure_notified = True
                            await db.commit()

                    log_action(
                        user="system",
                        action="rotation_permanent_failure",
                        details=f"Request {r.id} failed after {settings.rotation_max_retries} attempts",
                        request=None,
                        category="rotation",
                    )