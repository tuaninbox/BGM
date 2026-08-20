# api/logs.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from deps.auth import get_current_user
from models.account import Account
from core.audit_logger import LOG_PATH
import json

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/")
async def api_get_logs(
    current_user: Account = Depends(get_current_user),
    user: str | None = None,
    category: str | None = None,
    action: str | None = None,
    timestamp: str | None = None,
    search: str | None = None,
    limit: int = 300,
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    logs = []

    try:
        with open(LOG_PATH, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except:
                    continue

               # Normalize helper
                def norm(value):
                    return (value or "").lower()

                # Filters (all partial match)
                if user and user.lower() not in norm(entry.get("user")):
                    continue

                if category and category.lower() not in norm(entry.get("category")):
                    continue

                if action and action.lower() not in norm(entry.get("action")):
                    continue

                if timestamp and timestamp.lower() not in norm(entry.get("timestamp")):
                    continue

                if search:
                    text = json.dumps(entry).lower()
                    if search.lower() not in text:
                        continue

                logs.append(entry)

    except FileNotFoundError:
        logs = []

    logs = logs[-limit:]
    logs.reverse()

    return logs
