from tests.helpers import FakeApp
from core.security import generate_strong_password, generate_username
from core.vault import VaultClient
from core.device_loader import load_devices
from core.credential_loader import load_credentials
from core.config_loader import load_config
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import asyncio

import inspect
import asyncio

async def maybe_await(func, *args):
    if inspect.iscoroutinefunction(func):
        return await func(*args)
    result = func(*args)
    if inspect.isawaitable(result):
        return await result
    return result

class AccountManager:
    def __init__(self, vault: VaultClient, app):
        self.vault = vault
        self.app = app
        self.devices = [] # await load_devices(app.state.config,"NCP")  # real device list

    # ---------------------------------------------------------
    # BREAKGLASS ACCOUNTS
    # ---------------------------------------------------------
    def create_device_account(self, device_name: str):
        device_key = device_name.lower()

        username = generate_username(device_key)
        password = generate_strong_password()

        payload = {
            "username": username,
            "password": password,
            "device": device_key,
        }

        self.vault._kv2_write(f"breakglass/{device_key}", payload)
        return payload

    def read_device_account(self, device_name: str):
        device_key = device_name.lower()
        return self.vault._kv2_read(f"breakglass/{device_key}")

    def update_device_account(self, device_name: str, new_username=None, new_password=None):
        device_key = device_name.lower()
        existing = self.read_device_account(device_key)

        if not existing:
            return None

        if new_username:
            existing["username"] = new_username.lower()
        if new_password:
            existing["password"] = new_password

        self.vault._kv2_write(f"breakglass/{device_key}", existing)
        return existing

    def delete_device_account(self, device_name: str):
        device_key = device_name.lower()
        self.vault.client.secrets.kv.v2.delete_metadata_and_all_versions(
            mount_point=self.vault.tenant,
            path=f"breakglass/{device_key}",
        )
        return True

    def list_device_accounts(self):
        keys = self.vault.list_keys("breakglass")
        return [k.rstrip("/") for k in keys]

    # ---------------------------------------------------------
    # OTP ACCOUNTS
    # ---------------------------------------------------------
    def create_otp_account(self, username: str, otp_seed: str):
        key = username.lower()

        payload = {
            "app": key,
            "otp_seed": otp_seed,
            "issuer": "BreakglassApp",
        }

        self.vault._kv2_write(f"otp/{key}", payload)
        return payload

    def read_otp_account(self, username: str):
        key = username.lower()
        return self.vault._kv2_read(f"otp/{key}")

    def update_otp_account(self, username: str, new_seed=None):
        key = username.lower()
        existing = self.read_otp_account(key)

        if not existing:
            return None

        if new_seed:
            existing["otp_seed"] = new_seed

        self.vault._kv2_write(f"otp/{key}", existing)
        return existing

    def delete_otp_account(self, username: str):
        key = username.lower()
        self.vault.client.secrets.kv.v2.delete_metadata_and_all_versions(
            mount_point=self.vault.tenant,
            path=f"otp/{key}",
        )
        return True

    def list_otp_accounts(self):
        keys = self.vault.list_keys("otp")
        return [k.rstrip("/") for k in keys]

async def main():
    app = FakeApp()
    app.state.config = load_config()
    app.state.credential_loader = load_credentials
    app.state.device_loader = load_devices
    # print(f"config: {app.state.config}")

    # Choose tenant (same as FastAPI would do from current_user)
    tenant = "NCP"

    # Load devices for this tenant
    #devices = asyncio.run(maybe_await(load_devices, app.state.config, tenant))
    devices = await load_devices(app.state.config, tenant)


    vault = VaultClient(app, tenant=tenant)
    mgr = AccountManager(vault, app)

    # Create breakglass account for each device
    for device_name in devices:
        # print(f"Devicename: {device_name}")
        device_key = device_name.get("name").lower()
        acc = mgr.create_device_account(device_key)
        print(f"Created breakglass account for {device_name}: {acc}")

    # # Create breakglass account
    # acc = mgr.create_device_account("RouterA")
    # print(acc)

    # # Read breakglass
    # print(mgr.read_device_account("RouterA"))

    # # Update breakglass
    # mgr.update_device_account("RouterA", new_password=generate_strong_password())

    # # Delete breakglass
    # mgr.delete_device_account("RouterA")

    # # OTP
    # mgr.create_otp_account("tuan", "OTPSEED-XYZ")
    # print(mgr.read_otp_account("tuan"))

if __name__ == "__main__":
    asyncio.run(main())