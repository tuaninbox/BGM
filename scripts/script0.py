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

def get_client(addr_env, role_env, secret_env):
    client = hvac.Client(
        url=os.environ[addr_env],
        verify=False
    )
    client.auth.approle.login(
        role_id=os.environ[role_env],
        secret_id=os.environ[secret_env]
    )
    return client

# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--ansible-url", required=True)
    parser.add_argument("--ansible-token", required=True)
    args = parser.parse_args()

    tenant = args.tenant.lower()
    dry_run = args.dry_run
    csv_path = f"inventory/{tenant}/devices.csv"

    cloud = get_client("VAULT_ADDR_CLOUD", "VAULT_ROLE_ID_CLOUD", "VAULT_SECRET_ID_CLOUD")
    dc1 = get_client("VAULT_ADDR_DC1", "VAULT_ROLE_ID_DC1", "VAULT_SECRET_ID_DC1")
    dc2 = get_client("VAULT_ADDR_DC2", "VAULT_ROLE_ID_DC2", "VAULT_SECRET_ID_DC2")

    # LOAD CSV
    csv_devices = {}
    csv_groups = set()

    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            device = row["Host"].lower()
            group = row["Group"].lower()

            row["Host"] = device
            row["Group"] = group

            csv_devices[device] = row
            csv_groups.add(group)

    # LOAD EXISTING VAULT STATE (Cloud only for devices/groups)
    try:
        vault_devices = cloud.secrets.kv.v2.list_secrets(
            path=f"{tenant}"
        )["data"]["keys"]
    except Exception:
        vault_devices = []

    try:
        vault_groups = cloud.secrets.kv.v2.list_secrets(
            path=f"{tenant}/group"
        )["data"]["keys"]
    except Exception:
        vault_groups = []

    created_devices = []
    updated_devices = []
    deleted_devices = []
    created_groups = []
    updated_groups = []
    new_device_hosts_for_ansible = []

    # DEVICE SYNC (Cloud + DC1 + DC2)
    for dev in vault_devices:
        if dev not in csv_devices:
            deleted_devices.append(dev)
            if not dry_run:
                for client in (cloud, dc1, dc2):
                    client.secrets.kv.v2.delete_metadata(
                        path=f"{tenant}/{dev}"
                    )

    for dev, data in csv_devices.items():
        if dev not in vault_devices:
            created_devices.append(dev)
            new_device_hosts_for_ansible.append(dev)
        else:
            updated_devices.append(dev)

        if not dry_run:
            for client in (cloud, dc1, dc2):
                client.secrets.kv.v2.create_or_update_secret(
                    path=f"{tenant}/{dev}",
                    secret=data
                )

    # GROUP SYNC (Cloud only)
    for group in csv_groups:
        path = f"{tenant}/group/{group}"

        try:
            existing = cloud.secrets.kv.v2.read_secret(
                path=path
            )["data"]["data"]
        except Exception:
            existing = None

        if existing is None:
            created_groups.append(group)
            creds = {
                "username": generate_username(tenant),
                "password": generate_password()
            }
            if not dry_run:
                cloud.secrets.kv.v2.create_or_update_secret(
                    path=path,
                    secret=creds
                )
        else:
            username = existing.get("username", "").lower()

            if not username.startswith(tenant) or len(username) < len(tenant) + 10:
                existing["username"] = generate_username(tenant)
                updated_groups.append(group)

            if not dry_run:
                cloud.secrets.kv.v2.create_or_update_secret(
                    path=path,
                    secret=existing
                )

    # ANSIBLE FOR NEW DEVICES (breakglass account push)
    ansible_job_id = None
    if new_device_hosts_for_ansible and not dry_run:
        payload = {
            "tenant": tenant,
            "new_devices": new_device_hosts_for_ansible
        }
        r = requests.post(
            args.ansible_url,
            headers={"Authorization": f"Bearer {args.ansible_token}"},
            json=payload,
            timeout=30
        )
        r.raise_for_status()
        ansible_job_id = r.json().get("job_id")

    report = {
        "created_devices": created_devices,
        "updated_devices": updated_devices,
        "deleted_devices": deleted_devices,
        "created_groups": created_groups,
        "updated_groups": updated_groups,
        "new_devices_ansible_job_id": ansible_job_id,
        "dry_run": dry_run
    }

    report_text = json.dumps(report, indent=2)
    print(report_text)

    teams_webhook = "https://example.teams.webhook/url"
    notify_teams(teams_webhook, f"Vault Provisioning Report:\n{report_text}")

    notify_email(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="user@example.com",
        smtp_pass="password123",
        email_from="noreply@example.com",
        email_to="admin@example.com",
        message=f"Vault Provisioning Report:\n{report_text}"
    )

    print("Provisioning complete.")

if __name__ == "__main__":
    main()
