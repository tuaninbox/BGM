import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

from core.settings import settings
from models.request import BreakglassRequest  # adjust import
from core.db import AsyncSessionLocal  # this matches your code


PERTH_OFFSET = timedelta(hours=8)

async def migrate_breakglass_timestamps():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(BreakglassRequest))).scalars().all()

        print(f"Found {len(rows)} BreakglassRequest rows to migrate...")

        def convert(dt_str: str):
            if not dt_str:
                return None

            try:
                perth_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S.%f")
            except ValueError:
                perth_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")

            utc_dt = perth_dt - PERTH_OFFSET
            return utc_dt.replace(tzinfo=timezone.utc).isoformat()

        for r in rows:
            r.start_time = convert(r.start_time)
            r.end_time = convert(r.end_time)
            r.created_at = convert(r.created_at)
            r.approved_at = convert(r.approved_at)

        await db.commit()

    print("Migration completed: all timestamps converted to ISO8601 UTC.")

if __name__ == "__main__":
    asyncio.run(migrate_breakglass_timestamps())
