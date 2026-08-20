# app/core/otp.py
import pyotp
import base64
import qrcode
from io import BytesIO


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
