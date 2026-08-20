from fastapi import APIRouter, Depends, HTTPException, Request, Body, Form
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import pyotp, base64, qrcode
from io import BytesIO
from core.audit_logger import log_action
from core.db import get_db
from deps.auth import get_current_user_optional, get_current_user
from core.permissions import has_permission
from core.security import hash_password, verify_password, validate_password_complexity, PasswordValidationError, UsernameValidationError, validate_username
from core.logging import log_event
from models.account import Account
from schemas.account import AccountRead, AccountCreate, AccountUpdate, AccountSelfUpdate, OTPVerify
from core.vault import VaultClient

router = APIRouter(prefix="/api", tags=["accounts"])

# Admin Account Endpoinst
# ---------------------------------------------------------
# CREATE ACCOUNT
# ---------------------------------------------------------
@router.post("/accounts", response_model=AccountRead)
async def api_create_account(
    payload: AccountCreate,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    # Auth check
    if current_user is None or current_user.role != "admin":
        raise HTTPException(status_code=401, detail="Admin access required")

    # Permission check
    roles = request.app.state.roles
    if not has_permission(current_user.role, "create_accounts", roles):
        log_action(
            current_user.username,
            "account_create",
            f"Account Creation - Permission Denied",
            request,
            category="account",
        )
        raise HTTPException(status_code=403, detail="Permission denied")

    try:
        validate_username(payload.username)
    except UsernameValidationError as e:
        log_action(
            current_user.username,
            "account_create",
            f"Account Creation - User Invalid",
            request,
            category="account",
        )
        raise HTTPException(status_code=400, detail=str(e))
    
    # Password confirmation
    if payload.password != payload.confirm_password:
        log_action(
            current_user.username,
            "account_create",
            f"Account Creation - Passwords do not match",
            request,
            category="account",
        )
        raise HTTPException(status_code=400, detail="Passwords do not match")

    # Password complexity check
    try:
        validate_password_complexity(payload.password)
    except PasswordValidationError as e:
        log_action(
            current_user.username,
            "account_create",
            f"Account Creation - Password invalid",
            request,
            category="account",
        )
        raise HTTPException(status_code=400, detail=str(e))

    # Username uniqueness
    stmt = select(Account).where(Account.username == payload.username)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    # Create new account
    acc = Account(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
        tenant=payload.tenant,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        source=payload.source,
        profiles=payload.profiles or [],
    )

    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    log_action(
            current_user.username,
            "account_create",
            f"Account {acc.username} created successfully",
            request,
            category="account",
    )    
    return acc

# ---------------------------------------------------------
# LIST ACCOUNTS
# ---------------------------------------------------------
@router.get("/accounts", response_model=list[AccountRead])
async def list_accounts(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: Account | None = Depends(get_current_user_optional),
):
    stmt = select(Account)
    result = await db.execute(stmt)
    accounts = result.scalars().all()
    log_action(
        current_user,
        "account_query",
        f"Account View - List Account Page",
        request,
        category="account",
    )        
    return accounts

# ---------------------------------------------------------
# EDIT ACCOUNTS
# ---------------------------------------------------------
@router.post("/accounts/{user_id}", response_model=AccountRead)
async def api_edit_account(
    user_id: int,
    payload: AccountUpdate,
    request: Request,
    current_user: Account | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    # Auth check
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - Admin Access Require",
            request,
            category="account",
        )  
        raise HTTPException(status_code=401, detail="Admin access required")

    # Fetch user
    stmt = select(Account).where(Account.id == user_id)
    account = (await db.execute(stmt)).scalar_one_or_none()
    if not account:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - Account not found",
            request,
            category="account",
        )  
        raise HTTPException(status_code=404, detail="Account not found")

    # Prevent demoting last admin
    stmt = select(Account).where(Account.role == "admin")
    admins = (await db.execute(stmt)).scalars().all()
    if account.role == "admin" and payload.role != "admin" and len(admins) == 1:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - Can't change role of last admin",
            request,
            category="account",
        )  
        raise HTTPException(status_code=400, detail="Cannot change role of the last admin")

    # Username uniqueness
    if payload.username and payload.username != account.username:
        try:
            validate_username(payload.username)
        except UsernameValidationError as e:
            log_action(
                current_user.username,
                "account_modify",
                f"Account Modify - Username is not unique",
                request,
                category="account",
            )  
            raise HTTPException(status_code=400, detail=str(e))
        stmt = select(Account).where(Account.username == payload.username)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        account.username = payload.username

    # First/Last name update
    if payload.first_name is not None:
        account.first_name = payload.first_name

    if payload.last_name is not None:
        account.last_name = payload.last_name

    # Email update
    if payload.email:
        account.email = payload.email

    # Role update
    if payload.role:
        account.role = payload.role

    # Role update
    if payload.tenant:
        account.tenant = payload.tenant

    # Profiles update
    if payload.profiles is not None:
        account.profiles = payload.profiles

    # Password update
    if payload.new_password:
        if payload.new_password != payload.confirm_password:
            raise HTTPException(status_code=400, detail="Passwords do not match")
        account.password_hash = hash_password(payload.new_password)

        # Password complexity check
    try:
        validate_password_complexity(payload.new_password)
    except PasswordValidationError as e:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - Password Complexity didn't meet",
            request,
            category="account",
        )  
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    await db.refresh(account)

    return account

# ---------------------------------------------------------
# VIEW SELF ACCOUNT - Need to be before get("/accounts/{user_id}")
# ---------------------------------------------------------
@router.get("/accounts/me", response_model=AccountRead)
async def api_get_self(request: Request, current_user: Account = Depends(get_current_user)):
    log_action(
        current_user,
        "account_query",
        f"Account Query - View Self Account",
        request,
        category="account",
    )  
    return current_user

# ---------------------------------------------------------
# GET ACCOUNTS TO EDIT
# ---------------------------------------------------------
@router.get("/accounts/{user_id}", response_model=AccountRead)
async def api_get_account(
    request: Request,
    user_id: int,
    current_user: Account | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user.username,
            "account_query",
            f"Account query - View account to edit - refused due to permission",
            request,
            category="account",
        )  
        raise HTTPException(status_code=401, detail="Admin access required")

    stmt = select(Account).where(Account.id == user_id)
    account = (await db.execute(stmt)).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    log_action(
        current_user,
        "account_query",
        f"Account Query - View account to edit",
        request,
        category="account",
    )  
    return account

# ---------------------------------------------------------
# DELETE ACCOUNTS
# ---------------------------------------------------------
@router.delete("/accounts/{user_id}", response_model=list[AccountRead])
async def api_delete_account(
    request: Request,
    user_id: int,
    current_user: Account | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    # Must be admin
    if current_user is None or current_user.role != "admin":
        log_action(
            current_user.username,
            "account_delete",
            f"Account Delete - Delete account - Refused due to permission",
            request,
            category="account",
        )  
        raise HTTPException(status_code=401, detail="Admin access required")

    # Count admins BEFORE deleting
    stmt = select(Account).where(Account.role == "admin")
    admin_count = len((await db.execute(stmt)).scalars().all())


    # Fetch account
    stmt = select(Account).where(Account.id == user_id)
    account = (await db.execute(stmt)).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Prevent deleting last admin
    if account.role == "admin" and admin_count <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin account")

    # Delete
    await db.delete(account)
    await db.commit()

    # Return updated list
    stmt = select(Account)
    accounts = (await db.execute(stmt)).scalars().all()

    log_action(
        current_user,
        "account_delete",
        f"Account Delete - Account {Account.username} deleted by {current_user.username}",
        request,
        category="account",
    )  
    return accounts

# SELF ENDPOINTS

# ---------------------------------------------------------
# UPDATE SELF ACCOUNT
# ---------------------------------------------------------
@router.put("/account/{user_id}", response_model=AccountRead)
async def api_update_account_self(
    request: Request,
    user_id: int,
    payload: AccountSelfUpdate,
    current_user: Account | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    # Must be logged in
    if current_user is None:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - Update self account - refused due to permission",
            request,
            category="account",
        )  
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Only admin or self can update
    if current_user.role != "admin" and current_user.id != user_id:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - only admin can update others' account",
            request,
            category="account",
        )  
        raise HTTPException(status_code=403, detail="Forbidden")

    # Fetch account
    stmt = select(Account).where(Account.id == user_id)
    account = (await db.execute(stmt)).scalar_one_or_none()

    if not account:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - Account {Account.username} not found",
            request,
            category="account",
        )  
        raise HTTPException(status_code=404, detail="Account not found")

    # Verify current password
    if not payload.current_password or not verify_password(payload.current_password, account.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Username cannot be changed by self
    if payload.username is not None and payload.username != account.username:
        log_action(
            current_user.username,
            "account_modify",
            f"Account Modify - can't change own username {Account.username}",
            request,
            category="account",
        )  
        raise HTTPException(
            status_code=400,
            detail="You cannot change your username."
        )
    
    # Username uniqueness
    if payload.username and payload.username != account.username:
        stmt = select(Account).where(Account.username == payload.username)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=400, detail="Username already exists")
        account.username = payload.username

    
    # First name update
    if payload.first_name is not None:
        account.first_name = payload.first_name

    # Last name update
    if payload.last_name is not None:
        account.last_name = payload.last_name

    # Email update
    if payload.email:
        account.email = payload.email

    # Password update (only if user entered a new password)
    if payload.new_password:
        # Confirm match
        if payload.new_password != payload.confirm_password:
            raise HTTPException(status_code=400, detail="New passwords do not match")

        # Validate complexity BEFORE hashing
        try:
            validate_password_complexity(payload.new_password)
        except PasswordValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Now safe to update
        account.password_hash = hash_password(payload.new_password)

    await db.commit()
    await db.refresh(account)
    log_action(
        current_user,
        "account_modify",
        f"Account Modify - account {current_user.username} updated",
        request,
        category="account",
    )  
    return account


# Create OTP
@router.get("/otp_qr")
async def api_otp_qr(
    request: Request,
    current_user = Depends(get_current_user)
):
    roles = request.app.state.roles

    if not has_permission(current_user.role, "create_otp", roles):
        raise HTTPException(
            status_code=403,
            detail="Permission denied: cannot generate OTP."
        )

    tenant = current_user.tenant
    config = request.app.state.config
    vault = VaultClient(config, tenant=tenant)

    # Generate seed
    secret = pyotp.random_base32()

    # Store seed to pending store in Vault
    await vault.save_pending_seed(current_user.username, secret)

    # Build provisioning URI
    uri = pyotp.TOTP(secret).provisioning_uri(
        name=current_user.username,
        issuer_name="BreakglassApp",
    )

    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        box_size=4,
        border=2
    )
    qr.add_data(uri)
    qr.make(fit=True)

    buf = BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)

    b64 = base64.b64encode(buf.read()).decode("ascii")

    return {
        "status": "ok",
        "qr_base64": b64
    }

# OTP Verify
@router.post("/otp/verify")
async def api_otp_verify(
    request: Request,
    payload: OTPVerify,
    current_user = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    if current_user is None:
        return {"status": "error", "message": "Unauthorized"}

    if current_user.role not in ("approver", "requester_approver", "admin"):
        return {"status": "error", "message": "Permission Denied"}
    
    tenant = current_user.tenant
    vault = VaultClient(request.app.state.config, tenant=tenant)

    pending = await vault.get_pending_seed(current_user.username)
    if not pending or "otp_seed" not in pending:
        return {"status": "error", "message": "No pending OTP seed"}

    seed = pending["otp_seed"]
    code = payload.code

    print(f"Backend get seed: {seed}")
    if not seed:
        return {"status": "error", "message": "No pending OTP seed"}

    totp = pyotp.TOTP(seed)
    print(f"Backend code: {code}")

    if not totp.verify(code):
        return {"status": "error", "message": "Invalid code"}

    await vault.save_otp_secret(current_user.username, seed)

    # Delete pending seed
    await vault.delete_pending_seed(current_user.username)

    result = await db.execute(select(Account).where(Account.id == current_user.id))
    account = result.scalar_one_or_none()

    if account:
        account.otp_enabled = True
        await db.commit()
        await db.refresh(account)

    return {"status": "ok", "message": "OTP verified and saved"}


