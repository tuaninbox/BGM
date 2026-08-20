# app/core/otp.py
import pyotp
import base64
import qrcode
from io import BytesIO
from .vault import VaultClient
from models.account import Account
from sqlalchemy import select

def generate_otp_qr(username: str):
    secret = pyotp.random_base32()

    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=username, issuer_name="Breakglass Account Management")

    qr = qrcode.make(uri)
    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    return secret, qr_base64


def verify_otp(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code)




async def validate_otp_for_approver(config, db, approver_username: str, otp_code: str):
    """
    Validate OTP for the given approver.
    """
    # Load approver account
    stmt = select(Account).where(Account.username == approver_username)
    result = await db.execute(stmt)
    approver = result.scalar_one_or_none()

    if not approver:
        return False

    # Load OTP secret from vault
    vault = VaultClient(config, tenant=approver.tenant)
    otp_secret = await vault.get_otp_secret(approver.username)

    if not otp_secret:
        return False

    # Verify OTP
    totp = pyotp.TOTP(otp_secret)
    return totp.verify(otp_code)
