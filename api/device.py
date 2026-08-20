from fastapi import APIRouter, Body, HTTPException, Request, WebSocket, WebSocketDisconnect, Depends
from deps.auth import get_current_user_optional
from models.account import Account
from core.audit_logger import log_action
# from core.ssh_manager import ssh_manager
router = APIRouter(prefix="/connector", tags=["Device Connector"])


def get_device(request: Request, device_id: str) -> dict:
    devices = getattr(request.app.state, "devices", []) or []
    for device in devices:
        if device.get("id") == device_id:
            return device
    raise HTTPException(status_code=404, detail="Device not found")






