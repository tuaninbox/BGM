import aiosmtplib
from email.message import EmailMessage
import logging
import asyncio
import hmac
import hashlib
import base64
import json
from datetime import datetime, timedelta, timezone
from models.request import BreakglassRequest
from core.db import AsyncSession


from core.settings import settings
from core.audit_logger import log_action
from core.settings import settings


logger = logging.getLogger("email")
SECRET_KEY = settings.email_approval_secret

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
    # send_email(to=approver_email, subject="Breakglass Approval Needed", body=body)
    simulate_send_email(to=approver_email, subject="Breakglass Approval Needed", body=body)

def simulate_send_email(to: str, subject: str, body: str, html_body: str | None = None):
    print("------------------------------------------------------------")
    print("📧 EMAIL DEBUG OUTPUT")
    print("To:", to)
    print("Subject:", subject)
    print("Body:", body)
    if html_body:
        print("HTML Body:", html_body)
    print("------------------------------------------------------------")



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

async def send_email_approval_links(req: BreakglassRequest, db: AsyncSession):
    # Get all approvers
    result = await db.execute(
        select(Account).where(
            Account.role.in_(["approver", "requester_approver"]),
            Account.otp_enabled == True
        )
    )
    approvers = result.scalars().all()

    # Exclude requester
    approvers = [a for a in approvers if a.id != req.requester_id]

    if not approvers:
        return

    for approver in approvers:
        token = generate_email_approval_token(req.id, approver.id)
        approval_link = f"{settings.email_approval_domain}/api/requests/{req.id}/email-approve?token={token}"

        send_approval_email(
            approver.email,
            req.device_name,
            req.requester_username,
            approval_link
        )

def send_rotation_success_email(
    requester_email: str,
    device_name: str,
    account_username: str,
    finished_at: str,
):
    body = f"""
Breakglass account rotation has completed successfully.

Device: {device_name}
Account: {account_username}

Rotation finished at:
{finished_at}

You may now safely continue normal operations.
"""

    send_email(
        to=requester_email,
        subject="Breakglass Rotation Successful",
        body=body
    )

def send_rotation_success_email_html(
    requester_email: str,
    device_name: str,
    account_username: str,
    finished_at: str,
):
    subject = "Breakglass Rotation Successful"

    body = f"""\
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
    <h2 style="color: #2c7a7b;">Breakglass account rotation completed successfully</h2>

    <p>The breakglass account rotation has finished successfully.</p>

    <table style="border-collapse: collapse; margin-top: 10px;">
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">Device:</td>
        <td style="padding: 4px 8px;">{device_name}</td>
      </tr>
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">Account:</td>
        <td style="padding: 4px 8px;">{account_username}</td>
      </tr>
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">Finished at:</td>
        <td style="padding: 4px 8px;">{finished_at}</td>
      </tr>
    </table>

    <p style="margin-top: 15px;">
      You may now safely continue normal operations.
    </p>

    <p style="margin-top: 20px; font-size: 12px; color: #777;">
      This is an automated notification from the Breakglass system.
    </p>
  </body>
</html>
"""

    send_email(to=requester_email, subject=subject, body=body, is_html=True)

def send_rotation_failure_email(
    requester_email: str,
    device_name: str,
    account_username: str,
    error_message: str,
    first_failed_at: str,
):
    body = f"""
Breakglass account rotation has permanently failed.

Device: {device_name}
Account: {account_username}

First failure detected:
{first_failed_at}

Error:
{error_message}

Rotation has been retried multiple times and exceeded the allowed failure window.
Please investigate the rotation service or device connectivity.
"""

    send_email(
        to=requester_email,
        subject="Breakglass Rotation FAILED",
        body=body
    )


def send_rotation_failure_email_html(
    requester_email: str,
    device_name: str,
    account_username: str,
    error_message: str,
    first_failed_at: str,
):
    subject = "Breakglass Rotation FAILED"

    body = f"""\
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
    <h2 style="color: #c53030;">Breakglass account rotation permanently failed</h2>

    <p>
      The breakglass account rotation has failed after multiple retry attempts and exceeded the allowed failure window.
    </p>

    <table style="border-collapse: collapse; margin-top: 10px;">
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">Device:</td>
        <td style="padding: 4px 8px;">{device_name}</td>
      </tr>
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">Account:</td>
        <td style="padding: 4px 8px;">{account_username}</td>
      </tr>
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">First failure detected:</td>
        <td style="padding: 4px 8px;">{first_failed_at}</td>
      </tr>
      <tr>
        <td style="padding: 4px 8px; font-weight: bold;">Error:</td>
        <td style="padding: 4px 8px;">{error_message}</td>
      </tr>
    </table>

    <p style="margin-top: 15px;">
      Please investigate the rotation service, device connectivity, or credential store to restore rotation capability.
    </p>

    <p style="margin-top: 20px; font-size: 12px; color: #777;">
      This is an automated notification from the Breakglass system.
    </p>
  </body>
</html>
"""

    send_email(to=requester_email, subject=subject, body=body, is_html=True)

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
