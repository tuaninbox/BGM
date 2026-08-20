import aiosmtplib
from email.message import EmailMessage
import logging
import asyncio
import hmac
import hashlib
import base64
import json
from datetime import datetime, timedelta, timezone


from core.settings import settings
from core.audit_logger import log_action


logger = logging.getLogger("email")



from core.settings import settings

SECRET_KEY = settings.email_approval_secret

# async def get_approvers(db: AsyncSession, requester_id: int):
#     # Get all approvers
#     result = await db.execute(
#         select(Account).where(Account.role == "approver")
#     )
#     approvers = result.scalars().all()

#     # Exclude requester if they are also an approver
#     approvers = [a for a in approvers if a.id != requester_id]

#     return approvers

def generate_email_approval_token(req_id: int, approver_id: int):
    exp_ts = int((datetime.now(timezone.utc) + timedelta(minutes=10)).timestamp())
    payload = {
        "req_id": req_id,
        "approver_id": approver_id,
        "exp": exp_ts
    }

    payload_bytes = json.dumps(payload).encode()
    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode()

    signature = hmac.new(
        SECRET_KEY.encode(),
        payload_bytes,
        hashlib.sha256
    ).digest()

    signature_b64 = base64.urlsafe_b64encode(signature).decode()

    return f"{payload_b64}.{signature_b64}"


def validate_email_approval_token(token: str):
    try:
        payload_b64, signature_b64 = token.split(".")
    except ValueError:
        return None

    payload_bytes = base64.urlsafe_b64decode(payload_b64)
    signature = base64.urlsafe_b64decode(signature_b64)

    expected_signature = hmac.new(
        SECRET_KEY.encode(),
        payload_bytes,
        hashlib.sha256
    ).digest()

    if not hmac.compare_digest(signature, expected_signature):
        return None

    payload = json.loads(payload_bytes)

    if payload["exp"] < int(datetime.utcnow().timestamp()):
        return None

    return payload

def send_approval_email(approver_email: str, device_name: str, requester: str, approval_link: str):
    # Use your existing email client (SMTP, SendGrid, SES, etc.)
    body = f"""
A breakglass request requires your approval.

Device: {device_name}
Requester: {requester}

Approve here:
{approval_link}

This link expires in 10 minutes.
"""
    send_email(to=approver_email, subject="Breakglass Approval Needed", body=body)


def normalize_user(user):
    """
    Normalizes user input for logging:
    - Account model → use username + role
    - str → username only
    - None → anonymous
    """
    if hasattr(user, "username"):
        return user.username, getattr(user, "role", None)
    elif isinstance(user, str):
        return user, None
    else:
        return "anonymous", None


async def send_email(to: str, subject: str, body: str, request=None, user=None):
    """
    Reusable async email sender using SMTP settings from core/settings.py.
    Includes audit logging via log_action().
    """

    username, role = normalize_user(user)

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        smtp_kwargs = {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
        }

        # Optional authentication
        if settings.smtp_user and settings.smtp_password:
            smtp_kwargs["username"] = settings.smtp_user
            smtp_kwargs["password"] = settings.smtp_password
            smtp_kwargs["start_tls"] = True

        await aiosmtplib.send(msg, **smtp_kwargs)

        # Audit log
        if request:
            log_action(
                user,
                "email_sent",
                f"Email sent to {to} with subject '{subject}'",
                request,
                category="email",
            )

        logger.info(f"Email sent to {to}: {subject}")

    except Exception as e:
        if request:
            log_action(
                user,
                "email_failed",
                f"Failed to send email to {to}: {e}",
                request,
                category="email",
                status="error",
            )

        # Optional but useful for debugging
        logger.error(f"Failed to send email to {to}: {e}")

        raise


async def main():
    """
    Run this file directly to send a test email:
    python core/email.py
    """
    to = input("Send test email to: ").strip()
    subject = "Breakglass Test Email"
    body = "This is a test email from Breakglass."

    print(f"Sending email to {to}...")
    await send_email(to, subject, body)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
