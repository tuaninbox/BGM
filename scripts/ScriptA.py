#!/usr/bin/env python3
import argparse
import os
import secrets
import string
import json
import hvac
from datetime import datetime, timezone
import uuid

def generate_password():
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    group = lambda: ''.join(secrets.choice(chars) for _ in range(12))
    return "-".join(group() for _ in range(4))

def generate_username(tenant_name: str):
    tenant = tenant_name.lower()
    chars = string.ascii_lowercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(10))
    return f"{tenant}{suffix}"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--group", action="append", help="Group(s) to rotate; if omitted, rotate all")
    args = parser.parse_args()

    tenant = args.tenant.lower()

    client = hvac.Client(
        url=os.environ["VAULT_ADDR_CLOUD"],
        verify=False
    )
    client.auth.approle.login(
        role_id=os.environ["VAULT_ROLE_ID_CLOUD"],
        secret_id=os.environ["VAULT_SECRET_ID_CLOUD"]
    )

    try:
        vault_groups = client.secrets.kv.v2.list_secrets(
            path=f"{tenant}/group"
        )["data"]["keys"]
    except Exception:
        vault_groups = []

    if args.group:
        target_groups = [g.lower() for g in args.group if g.lower() in vault_groups]
    else:
        target_groups = vault_groups

    rotated = {}

    for group in target_groups:
        path = f"{tenant}/group/{group}"
        try:
            existing = client.secrets.kv.v2.read_secret(path=path)["data"]["data"]
        except Exception:
            continue

        old_username = existing.get("username")
        old_password = existing.get("password")

        new_username = generate_username(tenant)
        new_password = generate_password()

        rotation_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat() + "Z"

        secret = {
            "username": new_username,
            "password": new_password,
            "metadata": {
                "pending_rotation": True,
                "rotation_id": rotation_id,
                "rotation_timestamp": now,
                "old_username": old_username,
                "old_password": old_password,
                "new_username": new_username,
                "new_password": new_password,
                "ansible_job_id": None,
                "ansible_status": "pending",
                "commit_timestamp": None,
                "rollback_timestamp": None
            }
        }

        client.secrets.kv.v2.create_or_update_secret(path=path, secret=secret)
        rotated[group] = secret["metadata"]

    print(json.dumps({"tenant": tenant, "rotated_groups": rotated}, indent=2))

if __name__ == "__main__":
    main()
