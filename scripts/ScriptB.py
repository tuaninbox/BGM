#!/usr/bin/env python3
import argparse
import os
import json
import hvac
import requests

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--ansible-url", required=True)
    parser.add_argument("--ansible-token", required=True)
    args = parser.parse_args()

    tenant = args.tenant.lower()

    cloud = get_client("VAULT_ADDR_CLOUD", "VAULT_ROLE_ID_CLOUD", "VAULT_SECRET_ID_CLOUD")
    dc1 = get_client("VAULT_ADDR_DC1", "VAULT_ROLE_ID_DC1", "VAULT_SECRET_ID_DC1")
    dc2 = get_client("VAULT_ADDR_DC2", "VAULT_ROLE_ID_DC2", "VAULT_SECRET_ID_DC2")

    try:
        groups = cloud.secrets.kv.v2.list_secrets(
            path=f"{tenant}/group"
        )["data"]["keys"]
    except Exception:
        groups = []

    synced_groups = {}

    for group in groups:
        path = f"{tenant}/group/{group}"
        try:
            cloud_secret = cloud.secrets.kv.v2.read_secret(path=path)["data"]["data"]
        except Exception:
            continue

        username = cloud_secret.get("username")
        password = cloud_secret.get("password")
        metadata = cloud_secret.get("metadata", {})

        if not metadata.get("pending_rotation"):
            continue

        payload = {"username": username, "password": password}

        dc1.secrets.kv.v2.create_or_update_secret(path=path, secret=payload)
        dc2.secrets.kv.v2.create_or_update_secret(path=path, secret=payload)

        dc1_secret = dc1.secrets.kv.v2.read_secret(path=path)["data"]["data"]
        dc2_secret = dc2.secrets.kv.v2.read_secret(path=path)["data"]["data"]

        if dc1_secret != payload or dc2_secret != payload:
            # re-write Cloud values (payload) to both; treat as sync failure
            dc1.secrets.kv.v2.create_or_update_secret(path=path, secret=payload)
            dc2.secrets.kv.v2.create_or_update_secret(path=path, secret=payload)
            continue

        synced_groups[group] = {
            "username": username,
            "password": password,
            "rotation_id": metadata.get("rotation_id")
        }

    if not synced_groups:
        print(json.dumps({"tenant": tenant, "synced_groups": {}, "ansible_job_id": None}, indent=2))
        return

    ansible_payload = {
        "tenant": tenant,
        "groups": synced_groups
    }

    r = requests.post(
        args.ansible_url,
        headers={"Authorization": f"Bearer {args.ansible_token}"},
        json=ansible_payload,
        timeout=30
    )
    r.raise_for_status()
    job_info = r.json()
    job_id = job_info.get("job_id")

    for group, info in synced_groups.items():
        path = f"{tenant}/group/{group}"
        cloud_secret = cloud.secrets.kv.v2.read_secret(path=path)["data"]["data"]
        metadata = cloud_secret.get("metadata", {})
        metadata["ansible_job_id"] = job_id
        metadata["ansible_status"] = "pending"
        cloud_secret["metadata"] = metadata
        cloud.secrets.kv.v2.create_or_update_secret(path=path, secret=cloud_secret)

    print(json.dumps({"tenant": tenant, "synced_groups": synced_groups, "ansible_job_id": job_id}, indent=2))

if __name__ == "__main__":
    main()
