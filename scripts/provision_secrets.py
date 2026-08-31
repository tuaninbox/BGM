#!/usr/bin/env python3
import csv
import argparse
import os
import secrets
import string
import hvac
import smtplib
import json
import requests
from email.mime.text import MIMEText


# ============================================================
# GENERATORS
# ============================================================
def generate_password():
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    group = lambda: ''.join(secrets.choice(chars) for _ in range(12))
    return "-".join(group() for _ in range(4))

def generate_username(tenant_name: str):
    tenant = tenant_name.lower()
    chars = string.ascii_lowercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(10))
    return f"{tenant}{suffix}"

# ============================================================
# NOTIFICATIONS
# ============================================================
def notify_teams(webhook_url, message):
    try:
        payload = {"text": message}
        requests.post(webhook_url, json=payload, timeout=5)
    except Exception as e:
        print(f"[WARN] Teams notification failed: {e}")

def notify_email(smtp_host, smtp_port, smtp_user, smtp_pass, email_from, email_to, message):
    try:
        msg = MIMEText(message)
        msg["Subject"] = "Vault Provisioning Report"
        msg["From"] = email_from
        msg["To"] = email_to

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(email_from, [email_to], msg.as_string())
    except Exception as e:
        print(f"[WARN] Email notification failed: {e}")

# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tenant = args.tenant.lower()
    dry_run = args.dry_run
    csv_path = f"inventory/{tenant}/devices.csv"

    # ============================================================
    # VAULT CLIENT (hvac)
    # ============================================================
    client = hvac.Client(
        url=os.environ["VAULT_ADDR"],
        verify=False
    )

    client.auth.approle.login(
        role_id=os.environ["VAULT_ROLE_ID"],
        secret_id=os.environ["VAULT_SECRET_ID"]
    )

    # ============================================================
    # LOAD CSV
    # ============================================================
    group_devices = {}  # group → list of devices

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            device = row["Host"].lower()
            group = row["Group"].lower()

            group_devices.setdefault(group, []).append(device)

    csv_groups = set(group_devices.keys())

    # ============================================================
    # LOAD EXISTING VAULT GROUPS
    # ============================================================
    try:
        vault_groups = client.secrets.kv.v2.list_secrets(
            path=f"{tenant}/group"
        )["data"]["keys"]
    except Exception:
        vault_groups = []

    # ============================================================
    # DIFF COLLECTION
    # ============================================================
    created_groups = []
    updated_groups = []
    deleted_groups = []

    # ============================================================
    # DELETE GROUPS REMOVED FROM CSV
    # ============================================================
    for grp in vault_groups:
        if grp not in csv_groups:
            deleted_groups.append(grp)
            if not dry_run:
                client.secrets.kv.v2.delete_metadata(
                    path=f"{tenant}/group/{grp}"
                )

    # ============================================================
    # CREATE / UPDATE GROUPS
    # ============================================================
    # ============================================================
    # GROUP SYNC (with device diff reporting)
    # ============================================================
    for group, devices in group_devices.items():
        path = f"{tenant}/group/{group}"

        try:
            existing = client.secrets.kv.v2.read_secret(
                path=path
            )["data"]["data"]
        except Exception:
            existing = None

        # Prepare diff tracking
        added_devices = []
        removed_devices = []

        if existing is None:
            # New group entirely
            created_groups.append({
                "group": group,
                "added_devices": devices,
                "removed_devices": []
            })

            creds = {
                "username": generate_username(tenant),
                "password": generate_password(),
                "device_list": devices
            }

            if not dry_run:
                client.secrets.kv.v2.create_or_update_secret(
                    path=path,
                    secret=creds
                )

        else:
            # Existing group → compute device diff
            old_devices = set(existing.get("device_list", []))
            new_devices = set(devices)

            added_devices = sorted(list(new_devices - old_devices))
            removed_devices = sorted(list(old_devices - new_devices))

            # Username validation
            username = existing.get("username", "").lower()
            if not username.startswith(tenant) or len(username) < len(tenant) + 10:
                existing["username"] = generate_username(tenant)

            # Update device list
            existing["device_list"] = devices

            # Only mark group as updated if something changed
            if added_devices or removed_devices or existing["username"] != username:
                updated_groups.append({
                    "group": group,
                    "added_devices": added_devices,
                    "removed_devices": removed_devices
                })

            if not dry_run:
                client.secrets.kv.v2.create_or_update_secret(
                    path=path,
                    secret=existing
                )


    # ============================================================
    # REPORT
    # ============================================================
    report = {
        "created_groups": created_groups,
        "updated_groups": updated_groups,
        "deleted_groups": deleted_groups,
        "dry_run": dry_run
    }

    report_text = json.dumps(report, indent=2)
    print(report_text)

    # ============================================================
    # NOTIFICATIONS
    # ============================================================
    # teams_webhook = "https://example.teams.webhook/url"
    # notify_teams(teams_webhook, f"Vault Provisioning Report:\n{report_text}")

    # notify_email(
    #     smtp_host="smtp.example.com",
    #     smtp_port=587,
    #     smtp_user="user@example.com",
    #     smtp_pass="password123",
    #     email_from="noreply@example.com",
    #     email_to="admin@example.com",
    #     message=f"Vault Provisioning Report:\n{report_text}"
    # )

    # print("Provisioning complete.")

if __name__ == "__main__":
    main()
