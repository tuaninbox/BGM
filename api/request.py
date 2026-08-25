from fastapi import APIRouter, Request, Depends, Form, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from core.db import get_db
from schemas.request import BreakglassRequestCreate, BreakglassRequestUpdate
from models.account import Account
from models.request import BreakglassRequest
from deps.auth import get_current_user
from core.debug import debug_error, debug_print
from core.vault import VaultClient
from core.email import generate_email_approval_token, validate_email_approval_token, send_approval_email
from core.audit_logger import log_action
from core.settings import settings
from core.permissions import has_permission
from core.utils import to_local_time
from core.otp import validate_otp_for_approver, verify_otp
import pyotp

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/requests")
async def list_requests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Account = Depends(get_current_user),
):
    roles = request.app.state.roles

    # ---------------------------------------------------------
    # RBAC: user must have read_requests permission
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "read_requests", roles):
        return {"ok": False, "error": "Permission denied"}

    # ---------------------------------------------------------
    # Fetch all requests
    # ---------------------------------------------------------
    result = await db.execute(select(BreakglassRequest))
    requests = result.scalars().all()

    response = []

    for r in requests:
        # Fetch requester username
        requester = await db.get(Account, r.requester_id)
        requester_username = requester.username if requester else None

        # Fetch approver username (may be None)
        approver_username = None
        if r.approver_id:
            approver = await db.get(Account, r.approver_id)
            approver_username = approver.username if approver else None

        response.append({
            "id": r.id,
            "device_name": r.device_name,
            "account_username": r.account_username,

            "requester_id": r.requester_id,
            "requester_username": requester_username,

            "approver_id": r.approver_id,
            "approver_username": approver_username,

            "request_reason": r.request_reason,
            "approve_reason": r.approve_reason,

            # Time window
            "start_time": to_local_time(r.start_time) if r.start_time else None,
            "end_time": to_local_time(r.end_time) if r.end_time else None,

            # Status
            "status": r.status,

            # Timestamps
            "created_at": to_local_time(r.created_at) if r.created_at else None,
            "approved_at": to_local_time(r.approved_at) if r.approved_at else None,

            # Approval method: direct / otp / email
            "approval_method": r.approval_method,

            # ---------------------------------------------------------
            # Rotation information
            # ---------------------------------------------------------
            "rotation_status": r.rotation_status,     # queued / running / success / failed
            "rotation_error": r.rotation_error,       # error message or None
            "rotation_at": to_local_time(r.rotation_at) if r.rotation_at else None,
        })

    return {"ok": True, "requests": response}


@router.get("/approverlist", response_model=dict)
async def get_otp_approvers(
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Account).where(Account.otp_enabled == True)
    result = await db.execute(stmt)
    approvers = result.scalars().all()

    # Exclude current user
    approvers = [a for a in approvers if a.id != current_user.id]

    approver_list = [
        {
            "id": a.id,
            "username": a.username,
            "role": a.role,
        }
        for a in approvers
    ]
    print(f"approver list {approver_list}")
    return {"approver_list": approver_list}


@router.post("/requests/create")
async def create_request(
    request: Request,
    background_tasks: BackgroundTasks,
    device_name: str = Form(...),
    account_username: str = Form(...),
    start_time: datetime = Form(...),
    end_time: datetime = Form(...),
    request_reason: str = Form(...),
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ):
    roles = request.app.state.roles

    try:
        # ---------------------------------------------------------
        # Permission check (RBAC)
        # ---------------------------------------------------------
        if not has_permission(current_user.role, "request_bg_account", roles):
            return {
                "ok": False,
                "error": "You do not have permission to create requests",
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
            }

        # ---------------------------------------------------------
        # Convert incoming times to UTC datetime objects
        # ---------------------------------------------------------
        start_dt = start_time.astimezone(timezone.utc)
        end_dt = end_time.astimezone(timezone.utc)

        # ---------------------------------------------------------
        # Time validation
        # ---------------------------------------------------------
        if end_dt <= start_dt:
            return {
                "ok": False,
                "error": "End time must be after start time",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

        if end_dt > start_dt + timedelta(hours=72):
            return {
                "ok": False,
                "error": "End time cannot exceed 72 hours from start time",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

        # Build payload
        payload = BreakglassRequestCreate(
            device_name=device_name,
            account_username=account_username,
            requester_id=current_user.id,
            requester_username=current_user.username,
            start_time=start_dt,
            end_time=end_dt,
            request_reason=request_reason,
        )

        payload_dict = payload.model_dump()
        payload_dict["start_time"] = payload.start_time.isoformat()
        payload_dict["end_time"] = payload.end_time.isoformat()

        # ---------------------------------------------------------
        # Vault lookup
        # ---------------------------------------------------------
        vault = VaultClient(request.app.state.config, tenant="NCP")
        bgaccounts = await vault.get_breakglass_accounts()

        bg_lookup = {item["device"]: item for item in bgaccounts}
        bg = bg_lookup.get(payload.device_name)

        if not bg:
            return {
                "ok": False,
                "error": "Breakglass account not found for device",
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

        account_username = bg["username"]

        # ---------------------------------------------------------
        # DUPLICATE CHECK (with expiry handling)
        # ---------------------------------------------------------
        dup_query = await db.execute(
            select(BreakglassRequest).where(
                BreakglassRequest.device_name == payload.device_name,
                BreakglassRequest.account_username == account_username,
                BreakglassRequest.requester_id == current_user.id,
                BreakglassRequest.status == "pending"
            ).order_by(BreakglassRequest.id.desc())
        )

        pending_requests = dup_query.scalars().all()
        now = datetime.now(timezone.utc)

        active_requests = []
        expired_requests = []

        for r in pending_requests:
            try:
                end_dt_existing = datetime.fromisoformat(r.end_time)
            except Exception:
                expired_requests.append(r)
                continue

            if end_dt_existing < now:
                r.status = "expired"
                expired_requests.append(r)
            else:
                active_requests.append(r)

        if expired_requests:
            await db.commit()

        duplicate = active_requests[0] if active_requests else None

        if duplicate:
            return {
                "ok": False,
                "error": f"A pending request already exists (request_id={duplicate.id})",
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
            }

        # ---------------------------------------------------------
        # Create DB object
        # ---------------------------------------------------------
        req = BreakglassRequest(**payload_dict, status="pending")

        db.add(req)
        await db.commit()
        await db.refresh(req)

        # ---------------------------------------------------------
        # Approver lookup
        # ---------------------------------------------------------
        result = await db.execute(
            select(Account).where(
                Account.role.in_(["approver", "requester_approver"]),
                Account.otp_enabled == True
            )
        )
        approvers = result.scalars().all()
        approvers = [a for a in approvers if a.id != current_user.id]

        if not approvers:
            return {
                "ok": False,
                "error": "No approver available to approve request",
                "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
            }

        # Sending approval email for each approver
        for approver in approvers:
            debug_print("Approver resolved", {
                "id": approver.id,
                "username": approver.username,
                "email": approver.email
            })

            # Send approval email
            token = generate_email_approval_token(req.id, approver.id)
            approval_link = f"{settings.backend_url}/ui/requests/{req.id}/email-approve?token={token}"
            debug_print("Approval token", token)
            debug_print("Approval link", approval_link)

            # Send email in background
            background_tasks.add_task(
                send_approval_email,
                approver.email,
                req.device_name,
                req.requester_username,
                approval_link
            )

        # ---------------------------------------------------------
        # Log action
        # ---------------------------------------------------------
        log_action(
            current_user,
            "breakglass_request",
            f"Requested access for device {payload.device_name}",
            request,
            category="breakglass",
        )

        return {"ok": True, "request_id": req.id}

    except Exception as e:
        debug_error(e)
        return {
            "ok": False,
            "error": str(e),
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z"
        }

@router.post("/requests/{req_id}/approve")
async def approve_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Permission check (RBAC)
    # ---------------------------------------------------------
    can_approve = has_permission(current_user.role, "approve_bg_account", roles)
    can_requester_otp = has_permission(current_user.role, "approve_bg_account_otp", roles)

    if not (can_approve or can_requester_otp):
        log_action(
            current_user.username,
            "approve_bg_account",
            "Account Approval - Permission Denied",
            request,
            category="breakglass",
        )
        return {"ok": False, "error": "Permission denied"}

    # ---------------------------------------------------------
    # Parse form or JSON
    # ---------------------------------------------------------
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        otp_code = payload.get("otp_code")
        approver_username = payload.get("approver_name")
        approve_reason = payload.get("approve_reason")
    else:
        form = await request.form()
        otp_code = form.get("otp_code")
        approver_username = form.get("approver_name")
        approve_reason = form.get("approve_reason")

    if settings.debug:
        print(f"Backend code, approver, reason: {otp_code} {approver_username} {approve_reason}")

    # ---------------------------------------------------------
    # Load request
    # ---------------------------------------------------------
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        return {"ok": False, "error": "Request not found"}

    # ---------------------------------------------------------
    # Self-approval forbidden unless requester uses OTP
    # ---------------------------------------------------------
    if req.requester_id == current_user.id and not otp_code:
        return {"ok": False, "error": "You cannot approve your own request"}

    # ---------------------------------------------------------
    # OTP approval path
    # ---------------------------------------------------------
    if otp_code:
        # Load approver account
        stmt = select(Account).where(Account.username == approver_username)
        result = await db.execute(stmt)
        approver = result.scalar_one_or_none()

        if settings.debug:
            print(f"Approver from DB: {approver}")

        if not approver:
            return {"ok": False, "error": "Approver not found"}

        # Load OTP secret from vault
        vault = VaultClient(request.app.state.config, tenant=approver.tenant)
        seed = await vault.get_otp_secret(approver.username)

        if settings.debug:
            print(f"Account seed from vault: {seed}")

        if not seed:
            return {"ok": False, "error": "OTP secret not found"}

        totp = pyotp.TOTP(seed["otp_seed"])

        if settings.debug:
            print(f"TOTP: {totp}")

        if not totp.verify(otp_code):
            return {"ok": False, "error": "Invalid OTP code"}

        # ---------------------------------------------------------
        # Apply approval
        # ---------------------------------------------------------
        req.status = "approved"
        req.approver_id = approver.id
        req.approver_username = approver.username
        req.approve_reason = approve_reason
        req.approved_at = datetime.now(timezone.utc).isoformat()
        req.approval_method = "otp"

        await db.commit()
        await db.refresh(req)

        log_action(
            current_user,
            "breakglass_approve_otp",
            f"{approver.username} approved request {req.id} for device {req.device_name} (OTP)",
            request,
            category="breakglass",
        )

        return {"ok": True, "method": "otp"}

    # ---------------------------------------------------------
    # Direct approval path
    # ---------------------------------------------------------
    if not current_user.otp_enabled:
        return {"ok": False, "error": "Direct approval requires OTP-enabled approver"}

    req.status = "approved"
    req.approver_id = current_user.id
    req.approver_username = current_user.username
    req.approve_reason = approve_reason
    req.approved_at = datetime.now(timezone.utc).isoformat()
    req.approval_method = "direct"

    await db.commit()
    await db.refresh(req)

    log_action(
        current_user,
        "breakglass_approve",
        f"{current_user.username} approved request {req.id} for device {req.device_name} (direct)",
        request,
        category="breakglass",
    )

    return {"ok": True, "method": "direct"}


@router.post("/requests/{req_id}/reject")
async def reject_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Parse form or JSON
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        reject_reason = payload.get("reject_reason")
    else:
        form = await request.form()
        reject_reason = form.get("reject_reason")

    # Load request
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        raise HTTPException(404, "Request not found")

    # Self-rejection forbidden (same rule as approval)
    if req.requester_id == current_user.id:
        raise HTTPException(400, "You cannot reject your own request")

    # Only approvers can reject
    if current_user.role not in ("approver", "requester_approver"):
        raise HTTPException(403, "Forbidden")

    # Update request
    req.status = "rejected"
    req.approver_id = current_user.id
    req.approver_username = current_user.username
    req.approve_reason = reject_reason
    req.approved_at = datetime.now(timezone.utc).isoformat()
    req.approval_method = "reject"

    await db.commit()
    await db.refresh(req)

    # Audit log
    log_action(
        current_user,
        "breakglass_reject",
        f"Rejected request {req_id} for device {req.device_name}",
        request,
        category="breakglass",
    )

    return {"ok": True, "method": "reject"}


@router.get("/requests/{req_id}/email-approve")
async def email_approve_request(
    req_id: int,
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Validate token
    payload = validate_email_approval_token(token)
    if not payload:
        return {"ok": False, "error": "Invalid or expired approval token"}

    if payload["req_id"] != req_id:
        return {"ok": False, "error": "Token does not match request"}

    approver_id = payload["approver_id"]

    # Load approver
    approver = await db.scalar(select(Account).where(Account.id == approver_id))
    if not approver:
        return {"ok": False, "error": "Approver not found"}

    # Load request
    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    if not req:
        return {"ok": False, "error": "Request not found"}

    # If request has been approved
    if req.status != "pending":
        return {"ok": False, "error": f"Request already {req.status}"}

    # Prevent self-approval
    if req.requester_id == approver_id:
        return {"ok": False, "error": "Cannot approve your own request"}

    # Approve
    req.status = "approved"
    req.approver_id = approver_id
    req.approver_username = approver.username
    req.approve_reason = "Approved via email link"
    req.approved_at = datetime.now(timezone.utc).isoformat()
    req.approval_method = "email"

    await db.commit()

    # Audit log
    log_action(
        approver,
        "breakglass_approve_email",
        f"Email approved request {req_id}",
        request,
        category="breakglass",
    )

    return {"ok": True, "method": "email", "approved_by": approver.username}


@router.get("/requests/{req_id}/show-password")
async def api_show_password(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Permission check: requester must have request_bg_account
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return {"ok": False, "error": "Permission denied"}

    config = request.app.state.config
    tenant = current_user.tenant

    # ---------------------------------------------------------
    # Load request
    # ---------------------------------------------------------
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        return {"ok": False, "error": "Request not found"}

    # Only requester can view password
    if req.requester_id != current_user.id:
        return {"ok": False, "error": "Forbidden"}

    # Must be approved or used
    if req.status not in ("approved", "used"):
        return {"ok": False, "error": "Request is not approved"}

    # ---------------------------------------------------------
    # Fetch password from Vault
    # ---------------------------------------------------------
    vault = VaultClient(config, tenant=tenant)

    try:
        bgaccounts = await vault.get_breakglass_accounts()
    except Exception:
        return {"ok": False, "error": "Vault error"}

    bg_lookup = {item["device"]: item for item in bgaccounts}
    bg = bg_lookup.get(req.device_name)

    if not bg:
        return {"ok": False, "error": "Breakglass account not found"}

    password = bg.get("password")
    username = bg.get("username")

    if not password:
        return {"ok": False, "error": "Password not available"}

    # ---------------------------------------------------------
    # Mark request as USED (credential exposed)
    # ---------------------------------------------------------
    if req.status != "used":
        req.status = "used"
        req.used_at = datetime.now(timezone.utc).isoformat()
        await db.commit()
        await db.refresh(req)

    # ---------------------------------------------------------
    # Return password
    # ---------------------------------------------------------
    return {
        "ok": True,
        "password": password,
        "device": req.device_name,
        "username": username,
        "status": req.status,
        "used_at": req.used_at,
    }


@router.get("/requests/{req_id}/copy-password")
async def api_copy_password(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Permission check: requester must have request_bg_account
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return {"ok": False, "error": "Permission denied"}

    config = request.app.state.config
    tenant = current_user.tenant

    # ---------------------------------------------------------
    # Load request
    # ---------------------------------------------------------
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        return {"ok": False, "error": "Request not found"}

    # Only requester can copy password
    if req.requester_id != current_user.id:
        return {"ok": False, "error": "Forbidden"}

    # Must be approved or used
    if req.status not in ("approved", "used"):
        return {"ok": False, "error": "Request is not approved"}

    # ---------------------------------------------------------
    # Fetch password from Vault
    # ---------------------------------------------------------
    try:
        vault = VaultClient(config, tenant=tenant)
        bgaccounts = await vault.get_breakglass_accounts()
    except Exception:
        return {"ok": False, "error": "Vault error"}

    bg_lookup = {item["device"]: item for item in bgaccounts}
    bg = bg_lookup.get(req.device_name)

    if not bg:
        return {"ok": False, "error": "Breakglass account not found"}

    password = bg.get("password")
    username = bg.get("username")

    if not password:
        return {"ok": False, "error": "Password not available"}

    # ---------------------------------------------------------
    # Mark request as USED (credential exposed)
    # ---------------------------------------------------------
    if req.status != "used":
        req.status = "used"
        req.used_at = datetime.now(timezone.utc).isoformat()
        await db.commit()
        await db.refresh(req)

    # ---------------------------------------------------------
    # Return password
    # ---------------------------------------------------------
    return {
        "ok": True,
        "password": password,
        "device": req.device_name,
        "username": username,
        "status": req.status,
        "used_at": req.used_at,
    }


@router.post("/rotation-callback")
async def rotation_callback(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Validate machine token
    auth = request.headers.get("Authorization")
    # print(f"api/request rotation callback - auth header: {auth}")
    if auth != f"Bearer {settings.rotation_api_token}":
        return {
            "ok": False,
            "error": "Unauthorized",
            "timestamp": datetime.utc(timezone.utc).isoformat() + "Z"
        }

    req_id = payload.get("req_id")
    status = payload.get("status")
    error = payload.get("error")

    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    if not req:
        return {
            "ok": False,
            "error": "Request not found",
            "req_id": req_id,
            "timestamp": datetime.now(timezone.utc).isoformat() # + "Z"
        }

    req.rotation_status = status
    req.rotation_error = error

    # Set rotation_at only when rotation is finished
    if status in ("success", "failed"):
        req.rotation_at = datetime.now(timezone.utc).isoformat() # + "Z"

    await db.commit()

    return {
        "ok": True,
        "req_id": req_id,
        "rotation_status": status,
        "rotation_error": error,
        "rotation_at": req.rotation_at,
        "timestamp": datetime.now(timezone.utc).isoformat() # + "Z"
    }


@router.post("/requests/{req_id}/close")
async def api_close_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = request.app.state.roles

    # ---------------------------------------------------------
    # Permission check
    # ---------------------------------------------------------
    if not has_permission(current_user.role, "request_bg_account", roles):
        return {"ok": False, "error": "Permission denied"}

    # ---------------------------------------------------------
    # Load request
    # ---------------------------------------------------------
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        return {"ok": False, "error": "Request not found"}

    # Only requester can close
    if req.requester_id != current_user.id:
        return {"ok": False, "error": "Forbidden"}

    # Only approved or used requests can be closed
    if req.status not in ("approved", "used"):
        return {"ok": False, "error": f"Cannot close request in status '{req.status}'"}

    # ---------------------------------------------------------
    # Update status → closed
    # Queue rotation
    # ---------------------------------------------------------
    req.status = "closed"
    req.rotation_status = "pending"
    req.rotation_at = None
    req.rotation_error = None

    await db.commit()
    await db.refresh(req)

    return {
        "ok": True,
        "message": "Request closed and rotation queued",
        "req_id": req.id,
        "status": req.status,
        "rotation_status": req.rotation_status,
    }
