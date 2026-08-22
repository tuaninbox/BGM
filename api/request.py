from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta, timezone
from core.db import get_db
# from models.request import BreakglassRequest
from schemas.request import BreakglassRequestCreate, BreakglassRequestUpdate
from models.account import Account
from models.request import BreakglassRequest
from deps.auth import get_current_user
from core.debug import debug_error, debug_print
from core.vault import VaultClient
from core.email import generate_email_approval_token
from core.audit_logger import log_action
from core.settings import settings
from core.permissions import has_permission
from core.utils import to_local_time
from core.otp import validate_otp_for_approver, verify_otp
import pyotp

router = APIRouter(prefix="/api", tags=["accounts"])


@router.get("/requests")
async def list_requests(db: AsyncSession = Depends(get_db)):
    # Fetch all requests
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
            "requester_username": r.requester_username,

            "approver_id": r.approver_id,
            "approver_username": r.approver_username,

            "request_reason": r.request_reason,
            "approve_reason": r.approve_reason,

            "start_time": to_local_time(r.start_time) if r.start_time else None,
            "end_time": to_local_time(r.end_time) if r.end_time else None,

            "status": r.status,

            "created_at": to_local_time(r.created_at) if r.created_at else None,
            "approved_at": to_local_time(r.approved_at) if r.approved_at else None,

            "approval_method": r.approval_method,   # "direct", "otp"
        })

    return {"requests": response}


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
    device_name: str = Form(...),
    account_username: str = Form(...),
    start_time: datetime = Form(...),
    end_time: datetime = Form(...),
    request_reason: str = Form(...),
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    ):
    
    try:
        # ---------------------------------------------------------
        # 1. Convert incoming times to UTC datetime objects
        # ---------------------------------------------------------
        start_dt = start_time.astimezone(timezone.utc)
        end_dt = end_time.astimezone(timezone.utc)

        # ---------------------------------------------------------
        # 2. Validate using datetime objects (correct)
        # ---------------------------------------------------------
        if end_dt <= start_dt:
            raise HTTPException(400, "End time must be after start time")

        if end_dt > start_dt + timedelta(hours=72):
            raise HTTPException(400, "End time cannot exceed 72 hours from start time")

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

        # Convert datetime → ISO8601 for DB
        payload_dict["start_time"] = payload.start_time.isoformat()
        payload_dict["end_time"] = payload.end_time.isoformat()

        # Permission check
        if current_user.role not in ("requester", "requester_approver"):
            debug_print("Permission denied", current_user.role)
            raise HTTPException(403, "Forbidden")

        # Time validation
        debug_print("Time window", {
            "start_time": payload.start_time,
            "end_time": payload.end_time
        })

        # Vault lookup
        debug_print("Fetching breakglass accounts from Vault")

        vault = VaultClient(request.app.state.config, tenant="NCP")
        bgaccounts = await vault.get_breakglass_accounts()

        debug_print("Vault accounts count", len(bgaccounts))

        bg_lookup = {item["device"]: item for item in bgaccounts}
        bg = bg_lookup.get(payload.device_name)

        if not bg:
            debug_print("Breakglass account not found", payload.device_name)
            raise HTTPException(404, "Breakglass account not found for device")

        account_username = bg["username"]
        debug_print("Resolved breakglass username", account_username)
        # ---------------------------------------------------------
        # DUPLICATE CHECK
        # ---------------------------------------------------------
        debug_print("Checking for duplicate request", {
            "device_name": payload.device_name,
            "account_username": account_username,
            "requester_id": current_user.id
        })

        # ---------------------------------------------------------
        # DUPLICATE CHECK (with expiry handling)
        # ---------------------------------------------------------

        # 1. Fetch ALL pending requests for this device/user
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

        # 2. Check each request's end_time
        for r in pending_requests:
            try:
                end_dt = datetime.fromisoformat(r.end_time)
            except Exception:
                # If parsing fails, treat as expired
                expired_requests.append(r)
                continue

            if end_dt < now:
                # Expired → mark it
                r.status = "expired"
                expired_requests.append(r)
            else:
                # Still active
                active_requests.append(r)

        # 3. Commit expired updates (if any)
        if expired_requests:
            await db.commit()

        # 4. Determine duplicate
        duplicate = active_requests[0] if active_requests else None

        # 5. If duplicate exists → reject
        if duplicate:
            debug_print("Duplicate request detected", {
                "existing_request_id": duplicate.id
            })
            raise HTTPException(
                409,
                f"A pending request already exists (request_id={duplicate.id})"
            )

        # If no active requests remain → allow creation
        debug_print("No active duplicate requests found")

        # Create DB object
        req = BreakglassRequest(**payload.model_dump(), status="pending")

        debug_print("BreakglassRequest object created", req.__dict__)

        db.add(req)
        await db.commit()
        await db.refresh(req)

        debug_print("BreakglassRequest committed", {"request_id": req.id})

        # Approver lookup
        result = await db.execute(
            select(Account).where(
                Account.role.in_(["approver", "requester_approver"]),
                Account.otp_enabled == True
            )
        )   

        approvers = result.scalars().all()

        # Exclude requester if they are also an approver
        approvers = [a for a in approvers if a.id != current_user.id]
        
        if not approvers:
            debug_print("No approver found", payload.device_name)
            raise HTTPException(400, "No approver available to approve request")

        if not approvers:
            debug_print("No approvers found", payload.device_name)
            raise HTTPException(400, "No approver available for this device")

        for approver in approvers:
            debug_print("Approver resolved", {
                "id": approver.id,
                "username": approver.username,
                "email": approver.email
            })

            # Token + email
            token = generate_email_approval_token(req.id, approver.id)
            approval_link = f"{settings.backend_url}/api/requests/{req.id}/email-approve?token={token}"

            debug_print("Approval token", token)
            debug_print("Approval link", approval_link)

            # send_approval_email(
            #     approver.email,
            #     req.device_name,
            #     req.requester_username,
            #     approval_link
            # )

        debug_print("Approval emails sent to all approvers")


        # Log action
        log_action(
            current_user,
            "breakglass_request",
            f"Requested access for device {payload.device_name}",
            request,
            category="breakglass",
        )

        debug_print("Action logged")

        return {"ok": True, "request_id": req.id}

    except Exception as e:
        debug_error(e)
        raise


@router.post("/requests/{req_id}/approve")
async def approve_request(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roles = request.app.state.roles
    if not has_permission(current_user.role, "approve_bg_account", roles):
        log_action(
            current_user.username,
            "approve_bg_account",
            f"Account Approval - Permission Denied",
            request,
            category="breakglass",
        )
        raise HTTPException(status_code=403, detail="Permission denied")
    # Parse form or JSON
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

    if settings.debug == True:
        print(f"Backend code, approver, reason: {otp_code} {approver_username} {approve_reason}")
    # Load request
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if settings.debug == True:
        print(f"Request from DB: {req}")
    if not req:
        if settings.debug == True:
            print(f"request not found")
        raise HTTPException(404, "Request not found")

    # Self-approval forbidden
    if req.requester_id == current_user.id:
        if not otp_code:
            print(f"Can't approve own request")
            raise HTTPException(400, "You cannot approve your own request")

    # OTP approval path
    if otp_code:
        # is_valid = await validate_otp_for_approver(request.app.state.config, db, approver_username, otp_code)
        # if not is_valid:
        #     raise HTTPException(400, "Invalid OTP code")

        # Load approver account
        stmt = select(Account).where(Account.username == approver_username)
        result = await db.execute(stmt)
        approver = result.scalar_one_or_none()
        if settings.debug == True:
            print(f"Approver from DB: {approver}")
        if not approver:
            return False

        # Load OTP secret from vault
        vault = VaultClient(request.app.state.config, tenant=approver.tenant)
        seed = await vault.get_otp_secret(approver.username)
        if settings.debug == True:
            print(f"Account seed from vault: {seed}")
        # print(f"Seed {seed["otp_seed"]}")
        if not seed:
            # return False
            # print(f"OTP secret not found")
            raise HTTPException(400, "OTP secret not found")

        totp = pyotp.TOTP(seed["otp_seed"])

        if settings.debug == True:
            print(f"TOTP: {totp}")
        if not totp.verify(otp_code):
            if settings.debug == True:
                print(f"Invalid TOP code")
            raise HTTPException(400, "Invalid OTP code")
        
            
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
            f"OTP approved request {req_id} for device {req.device_name}",
            request,
            category="breakglass",
        )

        return {"ok": True, "method": "otp"}

    # Direct approval path
    # if current_user.role not in ("approver", "requester_approver"):
    #     raise HTTPException(403, "Forbidden")

    if not current_user.otp_enabled:
        if settings.debug == True:
            print(f"No approver with OTP enabled")
        raise HTTPException(400, "Direct approval requires OTP-enabled approver")

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
        f"Approved request {req_id} for device {req.device_name}",
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
    from core.security.email import validate_email_approval_token

    payload = validate_email_approval_token(token)
    if not payload:
        raise HTTPException(400, "Invalid or expired approval token")

    if payload["req_id"] != req_id:
        raise HTTPException(400, "Token does not match request")

    approver_id = payload["approver_id"]

    approver = await db.scalar(select(Account).where(Account.id == approver_id))
    if not approver:
        raise HTTPException(404, "Approver not found")

    req = await db.scalar(select(BreakglassRequest).where(BreakglassRequest.id == req_id))
    if not req:
        raise HTTPException(404, "Request not found")

    if req.requester_id == approver_id:
        raise HTTPException(400, "Cannot approve your own request")

    req.status = "approved"
    req.approver_id = approver_id
    req.approver_username = approver.username
    req.approve_reason = "Approved via email link"
    req.approved_at = datetime.utcnow()
    req.approval_method = "email"

    await db.commit()

    log_action(
        approver,
        "breakglass_approve_email",
        f"Email approved request {req_id}",
        request,
        category="breakglass",
    )

    return {"ok": True, "method": "email"}

@router.get("/requests/{req_id}/show-password")
async def api_show_password(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = request.app.state.config
    tenant = current_user.tenant

    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        return {"ok": False, "error": "Request not found"}

    if req.requester_id != current_user.id:
        return {"ok": False, "error": "Forbidden"}

    if req.status != "approved":
        return {"ok": False, "error": "Request is not approved"}

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

    return {
        "ok": True,
        "password": password,
        "device": req.device_name,
        "username": username,
    }

@router.get("/requests/{req_id}/copy-password")
async def api_copy_password(
    req_id: int,
    request: Request,
    current_user: Account = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    config = request.app.state.config
    tenant = current_user.tenant

    # Load request
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        return {"ok": False, "error": "Request not found"}

    # Only requester can copy password
    if req.requester_id != current_user.id:
        return {"ok": False, "error": "Forbidden"}

    # Must be approved
    if req.status != "approved":
        return {"ok": False, "error": "Request is not approved"}

    # Fetch password from Vault
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
    if not password:
        return {"ok": False, "error": "Password not available"}

    return {"ok": True, "password": password}
