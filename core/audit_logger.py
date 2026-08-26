import json
import os
from datetime import datetime,timezone
from loguru import logger
from fastapi import Request
from core.settings import settings

# Ensure log folder exists
os.makedirs(settings.log_folder, exist_ok=True)

LOG_PATH = os.path.join(settings.log_folder, settings.log_file)

# Configure rotating JSON log
logger.add(
    LOG_PATH,
    rotation=settings.log_rotation,       # e.g., "10 MB"
    retention=settings.log_retention,     # e.g., "14 days"
    compression=settings.log_compression, # e.g., "zip"
    format="{message}",                   # raw JSON only
)

def log_action(user, action: str, details: str, request= None,
               status="success", category="general"):

    # Only extract IP if this is a FastAPI Request object
    if request is not None and hasattr(request, "client"):
        ip = request.client.host
        path = request.url.path
    else:
        ip = None
        path = None

    # Normalize user
    if hasattr(user, "username"):
        username = user.username
        role = getattr(user, "role", None)
    elif isinstance(user, str):
        username = user
        role = None
    else:
        username = "anonymous"
        role = None

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user": username,
        "role": role,
        "action": action,
        "category": category,
        "details": details,
        "ip": ip,
        "path": path,
        "status": status,
    }

    logger.info(json.dumps(record))

