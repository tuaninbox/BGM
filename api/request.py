from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timedelta
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

router = APIRouter(prefix="/api", tags=["accounts"])

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db import get_db
from models.request import BreakglassRequest
from models.account import Account

router = APIRouter()


@router.get("/api/requests")
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
            "requester": requester_username,
            "request_reason": r.request_reason,
            "approve_reason": r.approve_reason,
            "start_time": r.start_time.isoformat() if r.start_time else None,
            "end_time": r.end_time.isoformat() if r.end_time else None,
            "status": r.status,
            "approver": approver_username,
        })

    return {"requests": response}


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
        payload = BreakglassRequestCreate(
            device_name=device_name,
            account_username=account_username,
            requester_id=current_user.id,
            requester_username=current_user.username,
            start_time=start_time,
            end_time=end_time,
            request_reason=request_reason,
            )
        debug_print("Incoming payload", payload.model_dump())
        debug_print("Current user", {"id": current_user.id, "role": current_user.role})

        # Permission check
        if current_user.role not in ("requester", "requester_approver"):
            debug_print("Permission denied", current_user.role)
            raise HTTPException(403, "Forbidden")

        # Time validation
        debug_print("Time window", {
            "start_time": payload.start_time,
            "end_time": payload.end_time
        })

        if payload.end_time <= payload.start_time:
            debug_print("Invalid time window", "end_time <= start_time")
            raise HTTPException(400, "End time must be after start time")

        if payload.end_time > payload.start_time + timedelta(hours=72):
            debug_print("Invalid time window", "end_time exceeds 72 hours")
            raise HTTPException(400, "End time cannot exceed 72 hours from start time")

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
    # Parse form or JSON
    if request.headers.get("content-type", "").startswith("application/json"):
        payload = await request.json()
        otp_code = payload.get("otp_code")
        approve_reason = payload.get("approve_reason")
    else:
        form = await request.form()
        otp_code = form.get("otp_code")
        approve_reason = form.get("approve_reason")

    # Load request
    stmt = select(BreakglassRequest).where(BreakglassRequest.id == req_id)
    result = await db.execute(stmt)
    req = result.scalar_one_or_none()

    if not req:
        raise HTTPException(404, "Request not found")

    # Self-approval forbidden
    if req.requester_id == current_user.id:
        raise HTTPException(400, "You cannot approve your own request")

    # OTP approval path
    if otp_code:
        if not validate_otp_for_approver(req.device_name, otp_code):
            raise HTTPException(400, "Invalid OTP code")

        req.status = "approved"
        req.approver_id = current_user.id
        req.approver_username = current_user.username
        req.approve_reason = approve_reason
        req.approved_at = datetime.utcnow()
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
    if current_user.role not in ("approver", "requester_approver"):
        raise HTTPException(403, "Forbidden")

    if not current_user.otp_enabled:
        raise HTTPException(400, "Direct approval requires OTP-enabled approver")

    req.status = "approved"
    req.approver_id = current_user.id
    req.approver_username = current_user.username
    req.approve_reason = approve_reason
    req.approved_at = datetime.utcnow()
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
