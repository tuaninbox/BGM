#!/usr/bin/env python3
import argparse
import os
import json
import time
import hvac
import requests
from datetime import datetime

MAX_WAIT_SECONDS = 21600   # 6 hours
POLL_INTERVAL = 10         # seconds
ROLLBACK_RETRIES = 5       # retry rollback up to 5 times
ROLLBACK_RETRY_DELAY = 15  # seconds between retries

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

def get_job_status(ansible_url, ansible_token, job_id):
    r = requests.get(
        f"{ansible_url.rstrip('/')}/jobs/{job_id}",
        headers={"Authorization": f"Bearer {ansible_token}"},
        timeout=30
    )
    r.raise_for_status()
    return r.json()

def rollback_device(ansible_url, ansible_token, tenant, device, username, password):
    payload = {
        "tenant": tenant,
        "device": device,
        "username": username,
        "password": password
    }

    try:
        r = requests.post(
            f"{ansible_url.rstrip('/')}/rollback-device",
            headers={"Authorization": f"Bearer {ansible_token}"},
            json=payload,
            timeout=30
        )
        r.raise_for_status()
        return True
    except Exception:
        return False

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--ansible-url", required=True)
    parser.add_argument("--ansible-token", required=True)
    parser.add_argument("--job-id", required=True, type=int)
    args = parser.parse_args()

    tenant = args.tenant.lower()
    job_id = args.job_id

    cloud = get_client("VAULT_ADDR_CLOUD", "VAULT_ROLE_ID_CLOUD", "VAULT_SECRET_ID_CLOUD")
    dc1 = get_client("VAULT_ADDR_DC1", "VAULT_ROLE_ID_DC1", "VAULT_SECRET_ID_DC1")
    dc2 = get_client("VAULT_ADDR_DC2", "VAULT_ROLE_ID_DC2", "VAULT_SECRET_ID_DC2")

    # ============================================================
    # ROTATION LOCK CHECK
    # ============================================================
    lock_path = f"{tenant}/rotation_lock"
    try:
        lock_data = cloud.secrets.kv.v2.read_secret(path=lock_path)["data"]["data"]
        if lock_data.get("locked", False):
            print(json.dumps({
                "error": "Rotation locked",
                "reason": lock_data.get("reason", "Unknown"),
                "timestamp": lock_data.get("timestamp")
            }, indent=2))
            return
    except Exception:
        pass  # no lock exists

    # Set lock
    cloud.secrets.kv.v2.create_or_update_secret(
        path=lock_path,
        secret={
            "locked": True,
            "reason": "Rotation in progress",
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
    )

    # ============================================================
    # MONITOR ANSIBLE JOB
    # ============================================================
    start = time.time()
    job_status = None
    job_result = None

    while True:
        job_result = get_job_status(args.ansible_url, args.ansible_token, job_id)
        job_status = job_result.get("status")

        if job_status in ("successful", "failed", "partial_success"):
            break

        if time.time() - start > MAX_WAIT_SECONDS:
            job_status = "failed"
            break

        time.sleep(POLL_INTERVAL)

    now = datetime.utcnow().isoformat() + "Z"

    # ============================================================
    # LOAD GROUPS
    # ============================================================
    try:
        groups = cloud.secrets.kv.v2.list_secrets(
            path=f"{tenant}/group"
        )["data"]["keys"]
    except Exception:
        groups = []

    result = {
        "tenant": tenant,
        "job_id": job_id,
        "ansible_status": job_status,
        "device_results": job_result.get("devices", {}),
        "groups": {}
    }

    # ============================================================
    # PARTIAL SUCCESS DETECTION
    # ============================================================
    device_results = job_result.get("devices", {})
    failed_devices = [d for d, s in device_results.items() if s != "success"]
    successful_devices = [d for d, s in device_results.items() if s == "success"]

    partial_success = len(failed_devices) > 0 and len(successful_devices) > 0

    # ============================================================
    # FULL SUCCESS → COMMIT ROTATION
    # ============================================================
    if job_status == "successful" and not partial_success:
        for group in groups:
            path = f"{tenant}/group/{group}"
            try:
                secret = cloud.secrets.kv.v2.read_secret(path=path)["data"]["data"]
            except Exception:
                continue

            metadata = secret.get("metadata", {})
            if metadata.get("ansible_job_id") != job_id:
                continue

            metadata["pending_rotation"] = False
            metadata["ansible_status"] = "successful"
            metadata["commit_timestamp"] = now
            secret["metadata"] = metadata

            cloud.secrets.kv.v2.create_or_update_secret(path=path, secret=secret)

            result["groups"][group] = {"action": "committed"}

        # Clear rotation lock
        cloud.secrets.kv.v2.create_or_update_secret(
            path=lock_path,
            secret={"locked": False, "reason": "Rotation committed", "timestamp": now}
        )

        print(json.dumps(result, indent=2))
        return

    # ============================================================
    # FAILURE OR PARTIAL SUCCESS → FULL ROLLBACK
    # ============================================================
    device_rollback_results = {}

    for group in groups:
        path = f"{tenant}/group/{group}"
        try:
            secret = cloud.secrets.kv.v2.read_secret(path=path)["data"]["data"]
        except Exception:
            continue

        metadata = secret.get("metadata", {})
        if metadata.get("ansible_job_id") != job_id:
            continue

        old_username = metadata.get("old_username")
        old_password = metadata.get("old_password")

        # Rollback CloudVault
        secret["username"] = old_username
        secret["password"] = old_password
        metadata["pending_rotation"] = False
        metadata["ansible_status"] = "failed"
        metadata["rollback_timestamp"] = now
        secret["metadata"] = metadata

        cloud.secrets.kv.v2.create_or_update_secret(path=path, secret=secret)

        # Rollback DC1/DC2
        payload = {"username": old_username, "password": old_password}
        dc1.secrets.kv.v2.create_or_update_secret(path=path, secret=payload)
        dc2.secrets.kv.v2.create_or_update_secret(path=path, secret=payload)

        result["groups"][group] = {"action": "rolled_back"}

    # ============================================================
    # DEVICE-LEVEL ROLLBACK WITH RETRIES
    # ============================================================
    for device in successful_devices:
        rollback_success = False

        for attempt in range(1, ROLLBACK_RETRIES + 1):
            ok = rollback_device(
                args.ansible_url,
                args.ansible_token,
                tenant,
                device,
                old_username,
                old_password
            )
            if ok:
                rollback_success = True
                break

            time.sleep(ROLLBACK_RETRY_DELAY)

        device_rollback_results[device] = (
            "rollback_success" if rollback_success else "rollback_failed"
        )

    result["device_rollback"] = device_rollback_results

    # ============================================================
    # CLEAR ROTATION LOCK
    # ============================================================
    cloud.secrets.kv.v2.create_or_update_secret(
        path=lock_path,
        secret={"locked": False, "reason": "Rotation rolled back", "timestamp": now}
    )

    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
