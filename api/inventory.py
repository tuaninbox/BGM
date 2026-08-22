from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from core.settings import settings
from core.db import get_db
from deps.auth import get_current_user
from core.logging import log_event
from core.device_loader import load_devices_from_file, load_devices
from models.account import Account
from models.request import BreakglassRequest
from core.permissions import has_permission
from core.audit_logger import log_action
from datetime import datetime, timezone
from core.vault import VaultClient
from core.utils import parse_iso8601
# from models.device import Device
# from schemas.device import DeviceRead, DeviceCreate, DeviceImportItem

router = APIRouter(prefix="/api", tags=["devices"])




@router.get("/devices")
async def list_devices(
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = request.app.state.config
    tenant = current_user.tenant

    # -------------------------------
    # Tenant validation
    # -------------------------------
    if "tenants" not in config or tenant not in config["tenants"]:
        return {"ok": False, "error": f"Tenant '{tenant}' not found in config", "devices": []}

    tenant_cfg = config["tenants"][tenant]

    if "devices" not in tenant_cfg:
        return {"ok": False, "error": f"Tenant '{tenant}' missing devices config", "devices": []}

    devices_cfg = tenant_cfg["devices"]
    source = devices_cfg["source"]

    try:
        has_approver = False

        # -------------------------------
        # Load devices (Nagios or file)
        # -------------------------------
        if source in ("nagios", "file"):
            devices = await load_devices(config, tenant)

            # -------------------------------
            # Vault lookup (breakglass accounts)
            # -------------------------------
            bg_lookup = {}
            try:
                vault = VaultClient(config, tenant="NCP")
                bgaccounts = await vault.get_breakglass_accounts()
                bg_lookup = {item["device"]: item for item in bgaccounts}
            except Exception as e:
                print(f"Failed to retrieve breakglass accounts: {str(e)}")

            merged_devices = []
            now_dt = datetime.now(timezone.utc)

            # -------------------------------
            # Build device list
            # -------------------------------
            for dev in devices:
                name = dev["name"]
                bg = bg_lookup.get(name)
                username = bg["username"] if bg else None

                # -------------------------------
                # Fetch ALL requests for this device/user
                # -------------------------------
                reqs = await db.execute(
                    select(BreakglassRequest).where(
                        BreakglassRequest.device_name == name,
                        BreakglassRequest.account_username == username,
                        BreakglassRequest.requester_id == current_user.id
                    ).order_by(BreakglassRequest.id.desc())
                )
                rows = reqs.scalars().all()

                # -------------------------------
                # Find active request (Python-side comparison)
                # -------------------------------
                active_request = None
                for r in rows:
                    if settings.debug:
                        print(f"api/inventory/listdevices - request: {r}")
                    end_dt = parse_iso8601(r.end_time)
                    if end_dt and end_dt >= now_dt:
                        active_request = r
                        break

                # -------------------------------
                # Build request info
                # -------------------------------
                request_info = None
                if active_request:
                    request_info = {
                        "id": active_request.id,
                        "status": active_request.status,
                        "start_time": active_request.start_time,
                        "end_time": active_request.end_time,
                        "requester_username": active_request.requester_username,
                        "approver_username": active_request.approver_username,
                    }
                if settings.debug:
                    print(f"api/inventory/listdevices - request_info: {request_info}")
                merged_devices.append({
                    **dev,
                    "username": username,
                    "request": request_info
                })

        else:
            return {
                "ok": False,
                "error": f"Invalid device source '{source}' for tenant '{tenant}'",
                "devices": []
            }

        # -------------------------------
        # Approver check
        # -------------------------------
        try:
            stmt = select(Account).where(
                Account.role.in_(["approver", "requester_approver"]),
                Account.otp_enabled == True
            )
            result = await db.execute(stmt)
            approver_count = len(result.scalars().all())
            has_approver = approver_count > 0
            if settings.debug:
                print(f"api/inventory/listdevices - merged devices: {merged_devices}")
            return {
                "ok": True,
                "count": len(devices),
                "devices": merged_devices,
                "has_approver": has_approver
            }

        except Exception:
            return {
                "ok": True,
                "count": len(devices),
                "devices": merged_devices,
            }

    except Exception as e:
        return {
            "ok": False,
            "error": f"Failed to load devices for tenant '{tenant}': {str(e)}",
            "devices": []
        }

# @router.get("/devices")
# async def list_devices(
#     request: Request,
#     current_user: Account = Depends(get_current_user),
# ):
#     cfg = request.app.state.config
#     source = cfg["devices"]["source"]

#     if source not in ("file", "nagios"):
#         return {
#             "ok": False,
#             "error": "Invalid device source configuration",
#             "devices": []
#         }

#     try:
#         devices = load_devices_from_file(cfg["devices"]["file_path"])
#         return {
#             "ok": True,
#             "count": len(devices),
#             "devices": devices
#         }
#     except Exception as e:
#         return {
#             "ok": False,
#             "error": f"Failed to load devices: {str(e)}",
#             "devices": []
#         }


@router.post("/devices/sync")
async def sync_devices_from_nagios(
    request: Request,
    current_user: Account = Depends(get_current_user),
):
    roles = request.app.state.roles

    # Permission check
    if not has_permission(current_user.role, "sync_nagios", roles):
        log_action(
            current_user.username,
            "device_sync",
            "Nagios Sync - Permission Denied",
            request,
            category="inventory",
        )
        return {"ok": False, "error": "Permission denied"}

    config = request.app.state.config
    tenant = current_user.tenant

    # Validate tenant
    if "tenants" not in config or tenant not in config["tenants"]:
        return {"ok": False, "error": f"Tenant '{tenant}' not found"}

    tenant_cfg = config["tenants"][tenant]

    if "devices" not in tenant_cfg:
        return {"ok": False, "error": f"Tenant '{tenant}' missing devices config"}

    # Try sync
    try:
        devices = await load_devices(config, tenant)
    except Exception as e:
        log_action(
            current_user.username,
            "device_sync",
            f"Nagios Sync - Failed: {str(e)}",
            request,
            category="inventory",
        )
        return {"ok": False, "error": f"Failed to sync devices: {str(e)}"}

    # Validate result
    if not devices:
        log_action(
            current_user.username,
            "device_sync",
            "Nagios Sync - Returned empty device list",
            request,
            category="inventory",
        )
        return {"ok": False, "error": "No devices returned from Nagios"}

    # Success
    log_action(
        current_user.username,
        "device_sync",
        f"Nagios Sync - Success ({len(devices)} devices)",
        request,
        category="inventory",
    )

    return {
        "ok": True,
        "count": len(devices),
        "devices": devices
    }

# @router.post("/devices/sync")
# async def sync_devices_from_nagios(
#     request: Request,
#     current_user: Account = Depends(get_current_user),
# ):
#     roles = request.app.state.roles

#     # Permission check
#     if not has_permission(current_user.role, "sync_nagios", roles):
#         log_action(
#             current_user.username,
#             "device_sync",
#             "Nagios Sync - Permission Denied",
#             request,
#             category="inventory",
#         )
#         return {
#             "ok": False,
#             "error": "Permission denied"
#         }

#     cfg = request.app.state.config

#     # Try sync
#     try:
#         devices = await load_devices(cfg)
#     except Exception as e:
#         log_action(
#             current_user.username,
#             "device_sync",
#             f"Nagios Sync - Failed: {str(e)}",
#             request,
#             category="inventory",
#         )
#         return {
#             "ok": False,
#             "error": f"Failed to sync devices from Nagios: {str(e)}"
#         }

#     # Validate result
#     if not devices or len(devices) == 0:
#         log_action(
#             current_user.username,
#             "device_sync",
#             "Nagios Sync - Returned empty device list",
#             request,
#             category="inventory",
#         )
#         return {
#             "ok": False,
#             "error": "No devices returned from Nagios"
#         }

#     # Success
#     log_action(
#         current_user.username,
#         "device_sync",
#         f"Nagios Sync - Success ({len(devices)} devices)",
#         request,
#         category="inventory",
#     )

#     return {
#         "ok": True,
#         "count": len(devices),
#         "devices": devices
#     }



# ---------------------------------------------------------
# NAGIOS SYNC
# ---------------------------------------------------------
# @router.post("/nagios/sync")
# async def sync_devices_from_nagios(
#     hostgroup_name: str,
#     current_user: Account = Depends(get_current_user),
# ):
#     if current_user.role != "admin":
#         raise HTTPException(status_code=403, detail="Only admin can sync devices")

#     # Load devices directly from Nagios
#     devices_data = await get_hosts_from_hostgroup(hostgroup_name)

#     # Log sync event (no DB write)
#     for d in devices_data:
#         log_event(
#             "DEVICE_SYNCED_FROM_NAGIOS",
#             name=d.get("name"),
#             hostgroup=hostgroup_name
#         )

#     return devices_data


# # ---------------------------------------------------------
# # IMPORT LOCAL DEVICES 
# # ---------------------------------------------------------
# @router.post("/import-local")
# async def import_local_devices(
#     items: list[dict],
#     current_user: Account = Depends(get_current_user),
# ):
#     if current_user.role != "admin":
#         raise HTTPException(status_code=403, detail="Only admin can import devices")

#     # Log import events (no DB write)
#     for item in items:
#         log_event(
#             "DEVICE_IMPORTED_LOCAL",
#             name=item.get("name")
#         )

#     # Just return the items back
#     return items