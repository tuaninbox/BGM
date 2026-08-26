import aiosmtplib
from fastapi import Request, BackgroundTasks
from email.message import EmailMessage
import logging
import asyncio
import hmac
import hashlib
import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from models.request import BreakglassRequest
from models.email_approval_token import EmailApprovalToken
from models.account import Account
from core.db import AsyncSession, AsyncSessionLocal
from sqlalchemy import insert, select, delete

from core.settings import settings
from core.audit_logger import log_action
from core.settings import settings


logger = logging.getLogger("email")
SECRET_KEY = settings.email_approval_secret

async def generate_email_approval_token(
    db: AsyncSession,
    req_id: int,
    approver_id: int,
):
    exp_ts = int(
        (datetime.now(timezone.utc) +
         timedelta(minutes=settings.email_approval_token_expiry_minutes)
        ).isoformat()
    )

    nonce = uuid.uuid4().hex  # 32-char random hex

    payload = {
        "req_id": req_id,
        "approver_id": approver_id,
        "exp": exp_ts,
        "nonce": nonce,
    }

    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()

    payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")

    signature = hmac.new(
        SECRET_KEY.encode(),
        payload_bytes,
        hashlib.sha256
    ).digest()

    signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

    token = f"{payload_b64}.{signature_b64}"

    # Store nonce in DB for replay protection
    await db.execute(insert(EmailApprovalToken).values(
            req_id=req_id,
            approver_id=approver_id,
            nonce=nonce,
            expiry_time=exp_ts,
        )
    )
    await db.commit()

    return token

async def validate_email_approval_token(token: str):
    if "." not in token:
        return {"ok": False, "error": "Malformed token"}

    payload_b64, signature_b64 = token.split(".", 1)

    def restore_padding(s: str):
        return s + "=" * (-len(s) % 4)

    try:
        payload_bytes = base64.urlsafe_b64decode(restore_padding(payload_b64))
        signature = base64.urlsafe_b64decode(restore_padding(signature_b64))
    except Exception:
        return {"ok": False, "error": "Invalid base64 encoding"}

    expected_signature = hmac.new(
        SECRET_KEY.encode(),
        payload_bytes,
        hashlib.sha256
    ).digest()

    if not hmac.compare_digest(signature, expected_signature):
        return {"ok": False, "error": "Invalid signature"}

    try:
        payload = json.loads(payload_bytes)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON payload"}

    required = ("req_id", "approver_id", "exp", "nonce")
    if not all(k in payload for k in required):
        return {"ok": False, "error": "Missing required fields"}

    try:
        req_id = int(payload["req_id"])
        approver_id = int(payload["approver_id"])
        exp = int(payload["exp"])
        nonce = str(payload["nonce"])
    except Exception:
        return {"ok": False, "error": "Invalid field types"}

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp < now_ts:
        return {"ok": False, "error": "Token expired"}

    return {
        "ok": True,
        "req_id": req_id,
        "approver_id": approver_id,
        "exp": exp,
        "nonce": nonce,
    }

def get_notification_recipients():
    return [
        *[
            email.strip()
            for email in settings.email_group_notification.split(",")
            if email.strip()
        ]
    ]

async def send_rotation_email(req: BreakglassRequest, success: bool):
    # Build recipient list: requester + group notification
    # recipients = [
    #     req.requester_email,
    #     *[
    #         email.strip()
    #         for email in settings.email_group_notification.split(",")
    #         if email.strip()
    #     ]
    # ]

    recipients = get_notification_recipients()

    if success:
        subject = "Breakglass Rotation Successful"

        body = f"""\
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
    <h2 style="color: #2c7a7b;">Breakglass account rotation completed successfully</h2>

    <p>The breakglass account rotation has finished successfully.</p>

    <table style="border-collapse: collapse; margin-top: 10px;">
      <tr><td style="padding: 4px 8px; font-weight: bold;">Device:</td><td style="padding: 4px 8px;">{req.device_name}</td></tr>
      <tr><td style="padding: 4px 8px; font-weight: bold;">Account:</td><td style="padding: 4px 8px;">{req.account_username}</td></tr>
      <tr><td style="padding: 4px 8px; font-weight: bold;">Finished at:</td><td style="padding: 4px 8px;">{req.rotation_at}</td></tr>
    </table>

    <p style="margin-top: 15px;">You may now safely continue normal operations.</p>

    <p style="margin-top: 20px; font-size: 12px; color: #777;">
      This is an automated notification from the Breakglass system.
    </p>
  </body>
</html>
"""
    else:
        subject = "Breakglass Rotation FAILED"

        body = f"""\
<html>
  <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
    <h2 style="color: #c53030;">Breakglass account rotation permanently failed</h2>

    <p>The breakglass account rotation has failed after multiple retry attempts and exceeded the allowed failure window.</p>

    <table style="border-collapse: collapse; margin-top: 10px;">
      <tr><td style="padding: 4px 8px; font-weight: bold;">Device:</td><td style="padding: 4px 8px;">{req.device_name}</td></tr>
      <tr><td style="padding: 4px 8px; font-weight: bold;">Account:</td><td style="padding: 4px 8px;">{req.account_username}</td></tr>
      <tr><td style="padding: 4px 8px; font-weight: bold;">First failure detected:</td><td style="padding: 4px 8px;">{req.rotation_first_error}</td></tr>
      <tr><td style="padding: 4px 8px; font-weight: bold;">Error:</td><td style="padding: 4px 8px;">{req.rotation_error}</td></tr>
    </table>

    <p style="margin-top: 15px;">Please investigate the rotation service, device connectivity, or credential store to restore rotation capability.</p>

    <p style="margin-top: 20px; font-size: 12px; color: #777;">
      This is an automated notification from the Breakglass system.
    </p>
  </body>
</html>
"""

    # Send ONE email to all recipients
    await send_email(
        to=recipients,
        subject=subject,
        body=body,
        is_html=True,
        request=req,
        user="system"
    )

async def send_rotation_summary(db: AsyncSession, period: str = "weekly"):
    now = datetime.now(timezone.utc)

    if period == "weekly":
        cutoff = now - timedelta(days=7)
        subject = "Weekly Breakglass Rotation Summary"
    elif period == "monthly":
        cutoff = now - timedelta(days=30)
        subject = "Monthly Breakglass Rotation Summary"
    else:
        raise ValueError("Invalid period: must be 'weekly' or 'monthly'")

    # Query finished rotations
    result = await db.execute(
        select(BreakglassRequest).where(
            BreakglassRequest.rotation_finished_at != None,
            BreakglassRequest.rotation_finished_at >= cutoff.isoformat()
        )
    )
    rows = result.scalars().all()

    # Recipients: group notification only
    recipients = [
        email.strip()
        for email in settings.email_group_notification.split(",")
        if email.strip()
    ]

    # Build HTML table
    if not rows:
        body = f"""
        <html>
          <body style="font-family: Arial; font-size: 14px;">
            <h2>{subject}</h2>
            <p>No breakglass rotations occurred during this period.</p>
          </body>
        </html>
        """
    else:
        rows_html = "".join([
            f"""
            <tr>
              <td style="padding: 6px; border: 1px solid #ccc;">{r.device_name}</td>
              <td style="padding: 6px; border: 1px solid #ccc;">{r.account_username}</td>
              <td style="padding: 6px; border: 1px solid #ccc;">{r.rotation_finished_at}</td>
              <td style="padding: 6px; border: 1px solid #ccc;">{r.status}</td>
            </tr>
            """
            for r in rows
        ])

        body = f"""
        <html>
          <body style="font-family: Arial; font-size: 14px;">
            <h2>{subject}</h2>

            <table style="border-collapse: collapse; margin-top: 10px;">
              <tr>
                <th style="padding: 6px; border: 1px solid #ccc;">Device</th>
                <th style="padding: 6px; border: 1px solid #ccc;">Account</th>
                <th style="padding: 6px; border: 1px solid #ccc;">Finished At</th>
                <th style="padding: 6px; border: 1px solid #ccc;">Status</th>
              </tr>
              {rows_html}
            </table>

            <p style="margin-top: 20px; font-size: 12px; color: #777;">
              This is an automated summary from the Breakglass system.
            </p>
          </body>
        </html>
        """

    send_email(
        to=recipients,
        subject=subject,
        body=body,
        is_html=True,
        request=None,
        user="system"
    )

def send_approval_email(
    email: str,
    device_name: str,
    requester: str,
    approval_link: str,
):
    subject = "Breakglass Approval Request"

    body = f"""
A breakglass request requires your approval.

Device: {device_name}
Requester: {requester}

Approve here:
{approval_link}

This link expires in {settings.email_approval_token_expiry_minutes} minutes.
"""

    html_body = f"""
<html>
<body>
    <h2>Breakglass Approval Required</h2>
    <p>A breakglass request requires your approval.</p>
    <p><strong>Device:</strong> {device_name}<br>
       <strong>Requester:</strong> {requester}</p>
    <p><a href="{approval_link}">Approve Request</a></p>
    <p>This link expires in <strong>{settings.email_approval_token_expiry_minutes} minutes</strong>.</p>
</body>
</html>
""".strip()

    # Simulation mode: print to console
    if settings.email_simulation:
        print("------------------------------------------------------------")
        print("📧 EMAIL SIMULATION")
        print("To:", email)
        print("Subject:", subject)
        print("Body:", body)
        print("------------------------------------------------------------")
        return

    # Real email mode
    send_email(
        to=email,
        subject=subject,
        body=html_body,
        is_html=True,
    )



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


async def send_email(to, subject: str, body: str, request=None, user=None, is_html=False):
    """
    Reusable async email sender using SMTP settings from core/settings.py.
    Supports multiple recipients (list or comma-separated string).
    Includes audit logging via log_action().
    """

    # Normalize recipients
    if isinstance(to, str):
        # Allow comma-separated list
        recipients = [email.strip() for email in to.split(",") if email.strip()]
    else:
        # Already a list
        recipients = to

    username, role = normalize_user(user)

    msg = EmailMessage()
    msg["From"] = settings.email_from
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if is_html:
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)

    try:
        smtp_kwargs = {
            "hostname": settings.smtp_host,
            "port": settings.smtp_port,
        }

        if settings.smtp_user and settings.smtp_password:
            smtp_kwargs["username"] = settings.smtp_user
            smtp_kwargs["password"] = settings.smtp_password
            smtp_kwargs["start_tls"] = True

        await aiosmtplib.send(msg, **smtp_kwargs)

        if request:
            log_action(
                user,
                "email_sent",
                f"Email sent to {recipients} with subject '{subject}'",
                request,
                category="email",
            )

        # logger.info(f"Email sent to {recipients}: {subject}")

    except Exception as e:
        if request:
            log_action(
                user,
                "email_failed",
                f"Failed to send email to {recipients}: {e}",
                request,
                category="email",
                status="error",
            )

        # logger.error(f"Failed to send email to {recipients}: {e}")
        # raise


async def send_email_approval_links(
    req: BreakglassRequest,
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    request: Request,
):
    # Lookup approvers
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
        log_action(
            None,
            "breakglass_request_no_approver",
            f"No approvers available for request {req.id}",
            request,
            category="breakglass",
            status="warning",
        )
        return

    for approver in approvers:
        token = await generate_email_approval_token(db,req.id, approver.id)

        approval_link = (
            f"{settings.email_approval_domain}/ui/requests/{req.id}/email-approve"
            f"?token={token}"
        )

        
        # Queue email
        background_tasks.add_task(
            send_approval_email,
            approver.email,
            req.device_name,
            req.requester_username,
            approval_link,
            # request,
            # None,  # user not needed
        )

        # Log
        log_action(
            None,
            "breakglass_approval_email_queued",
            f"Queued approval email for approver {approver.username} ({approver.email}) "
            f"for request {req.id}",
            request,
            category="breakglass",
        )


async def cleanup_email_approval_tokens():
    """
    Runs every minute.
    Expires email approval tokens whose expiry_time has passed.
    Tokens do not have a status field — lifecycle is based on used_at + expiry_time.
    """

    now_dt = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        # Load all tokens
        result = await db.execute(select(EmailApprovalToken))
        tokens = result.scalars().all()

        changed = False

        for t in tokens:
            expiry_dt = t.expiry_time

            # Skip if no expiry_time
            if not expiry_dt:
                continue

            # Convert ISO string → datetime if needed
            if isinstance(expiry_dt, str):
                expiry_dt = datetime.fromisoformat(expiry_dt)

            # 1. Expired but unused → mark as used (prevents replay)
            if t.used_at is None and expiry_dt < now_dt:
                t.used_at = expiry_dt  # mark as expired
                changed = True

                log_action(
                    user="system",
                    action="email_token_expired",
                    details=f"Email token {t.id} expired automatically",
                    request=None,
                    category="cleanup",
                )

        if changed:
            await db.commit()
            print("✔ Cleanup: email approval tokens updated")


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


# No replay protection
# def validate_email_approval_token(token: str):
#     # Basic format check
#     if "." not in token:
#         return {"ok": False, "error": "Malformed token"}

#     payload_b64, signature_b64 = token.split(".", 1)

#     # Restore padding if stripped by email clients
#     def restore_padding(s: str):
#         return s + "=" * (-len(s) % 4)

#     try:
#         payload_bytes = base64.urlsafe_b64decode(restore_padding(payload_b64))
#         signature = base64.urlsafe_b64decode(restore_padding(signature_b64))
#     except Exception:
#         return {"ok": False, "error": "Invalid base64 encoding"}

#     # Verify signature
#     expected_signature = hmac.new(
#         SECRET_KEY.encode(),
#         payload_bytes,
#         hashlib.sha256
#     ).digest()

#     if not hmac.compare_digest(signature, expected_signature):
#         return {"ok": False, "error": "Invalid signature"}

#     # Parse JSON
#     try:
#         payload = json.loads(payload_bytes)
#     except json.JSONDecodeError:
#         return {"ok": False, "error": "Invalid JSON payload"}

#     # Required fields
#     required = ("req_id", "approver_id", "exp")
#     if not all(k in payload for k in required):
#         return {"ok": False, "error": "Missing required fields"}

#     # Type checks
#     try:
#         req_id = int(payload["req_id"])
#         approver_id = int(payload["approver_id"])
#         exp = int(payload["exp"])
#     except Exception:
#         return {"ok": False, "error": "Invalid field types"}

#     # Expiry check
#     now_ts = int(datetime.now(timezone.utc).timestamp())
#     if exp < now_ts:
#         return {"ok": False, "error": "Token expired"}

#     return {
#         "ok": True,
#         "req_id": req_id,
#         "approver_id": approver_id,
#         "exp": exp,
#     }



# No replay protection
# def generate_email_approval_token(req_id: int, approver_id: int):
#     exp_ts = int(
#         (datetime.now(timezone.utc) +
#          timedelta(minutes=settings.email_approval_token_expiry_minutes)
#         ).timestamp()
#     )

#     payload = {
#         "req_id": req_id,
#         "approver_id": approver_id,
#         "exp": exp_ts,
#     }

#     payload_bytes = json.dumps(payload, separators=(",", ":")).encode()

#     payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=")

#     signature = hmac.new(
#         SECRET_KEY.encode(),
#         payload_bytes,
#         hashlib.sha256
#     ).digest()

#     signature_b64 = base64.urlsafe_b64encode(signature).decode().rstrip("=")

#     return f"{payload_b64}.{signature_b64}"



#def send_rotation_success_email(
#     requester_email: str,
#     device_name: str,
#     account_username: str,
#     finished_at: str,
# ):
#     body = f"""
# Breakglass account rotation has completed successfully.

# Device: {device_name}
# Account: {account_username}

# Rotation finished at:
# {finished_at}

# You may now safely continue normal operations.
# """

#     send_email(
#         to=requester_email,
#         subject="Breakglass Rotation Successful",
#         body=body
#     )

# def send_rotation_success_email_html(
#     requester_email: str,
#     device_name: str,
#     account_username: str,
#     finished_at: str,
# ):
#     subject = "Breakglass Rotation Successful"

#     body = f"""\
# <html>
#   <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
#     <h2 style="color: #2c7a7b;">Breakglass account rotation completed successfully</h2>

#     <p>The breakglass account rotation has finished successfully.</p>

#     <table style="border-collapse: collapse; margin-top: 10px;">
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">Device:</td>
#         <td style="padding: 4px 8px;">{device_name}</td>
#       </tr>
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">Account:</td>
#         <td style="padding: 4px 8px;">{account_username}</td>
#       </tr>
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">Finished at:</td>
#         <td style="padding: 4px 8px;">{finished_at}</td>
#       </tr>
#     </table>

#     <p style="margin-top: 15px;">
#       You may now safely continue normal operations.
#     </p>

#     <p style="margin-top: 20px; font-size: 12px; color: #777;">
#       This is an automated notification from the Breakglass system.
#     </p>
#   </body>
# </html>
# """

#     send_email(to=requester_email, subject=subject, body=body, is_html=True)

# def send_rotation_failure_email(
#     requester_email: str,
#     device_name: str,
#     account_username: str,
#     error_message: str,
#     first_failed_at: str,
# ):
#     body = f"""
# Breakglass account rotation has permanently failed.

# Device: {device_name}
# Account: {account_username}

# First failure detected:
# {first_failed_at}

# Error:
# {error_message}

# Rotation has been retried multiple times and exceeded the allowed failure window.
# Please investigate the rotation service or device connectivity.
# """

#     send_email(
#         to=requester_email,
#         subject="Breakglass Rotation FAILED",
#         body=body
#     )


# def send_rotation_failure_email_html(
#     requester_email: str,
#     device_name: str,
#     account_username: str,
#     error_message: str,
#     first_failed_at: str,
# ):
#     subject = "Breakglass Rotation FAILED"

#     body = f"""\
# <html>
#   <body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
#     <h2 style="color: #c53030;">Breakglass account rotation permanently failed</h2>

#     <p>
#       The breakglass account rotation has failed after multiple retry attempts and exceeded the allowed failure window.
#     </p>

#     <table style="border-collapse: collapse; margin-top: 10px;">
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">Device:</td>
#         <td style="padding: 4px 8px;">{device_name}</td>
#       </tr>
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">Account:</td>
#         <td style="padding: 4px 8px;">{account_username}</td>
#       </tr>
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">First failure detected:</td>
#         <td style="padding: 4px 8px;">{first_failed_at}</td>
#       </tr>
#       <tr>
#         <td style="padding: 4px 8px; font-weight: bold;">Error:</td>
#         <td style="padding: 4px 8px;">{error_message}</td>
#       </tr>
#     </table>

#     <p style="margin-top: 15px;">
#       Please investigate the rotation service, device connectivity, or credential store to restore rotation capability.
#     </p>

#     <p style="margin-top: 20px; font-size: 12px; color: #777;">
#       This is an automated notification from the Breakglass system.
#     </p>
#   </body>
# </html>
# """

#     send_email(to=requester_email, subject=subject, body=body, is_html=True)

