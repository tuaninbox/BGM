
import os
import secrets
import string
import hvac
import asyncio

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
# -----------------------------
# Fake minimal app object
# -----------------------------
class FakeApp:
    class State:
        config = {
            "tenants": {
                "NCP": {
                    "vault": {
                        "vault_address": "https://mint.home.tuan.au:8200",
                        "approle_id_file": "approle_id",
                        "secret_id_file": "approle_secretid",
                    }
                }
            }
        }

    state = State()



class VaultClient:
    def __init__(self, config, tenant="NCP"):
        self.config = config
        self.tenant = tenant

        tenant_cfg = config["tenants"][self.tenant]
        vault_cfg = tenant_cfg["vault"]

        self.vault_addr = vault_cfg["vault_address"]
        self.role_id_file = vault_cfg["approle_id_file"]
        self.secret_id_file = vault_cfg["secret_id_file"]

        # Load AppRole credentials (sync)
        role_id = self._load_file(self.role_id_file)
        secret_id = self._load_file(self.secret_id_file)

        # Create hvac client (sync)
        self.client = hvac.Client(
            url=self.vault_addr,
            verify=False,
        )

        # Login using AppRole (sync)
        self.client.auth.approle.login(
            role_id=role_id,
            secret_id=secret_id,
        )

    # -----------------------------
    # Internal helpers
    # -----------------------------
    def _load_file(self, path: str) -> str:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Credential file not found: {path}")
        with open(path, "r") as f:
            return f.read().strip()

    # -----------------------------
    # KV v2 helpers (sync versions)
    # -----------------------------
    def _kv2_write_sync(self, path: str, data: dict):
        self.client.secrets.kv.v2.create_or_update_secret(
            mount_point=self.tenant,
            path=path,
            secret=data,
        )
        return True

    def _kv2_read_sync(self, path: str):
        try:
            resp = self.client.secrets.kv.v2.read_secret(
                mount_point=self.tenant,
                path=path,
            )
            return resp["data"]["data"]
        except hvac.exceptions.InvalidPath:
            return None

    def _list_keys_sync(self, path: str):
        try:
            resp = self.client.secrets.kv.v2.list_secrets(
                mount_point=self.tenant,
                path=path,
            )
            return resp["data"]["keys"]
        except hvac.exceptions.InvalidPath:
            return []

    # -----------------------------
    # ASYNC wrappers
    # -----------------------------
    async def kv2_write(self, path: str, data: dict):
        return await asyncio.to_thread(self._kv2_write_sync, path, data)

    async def kv2_read(self, path: str):
        return await asyncio.to_thread(self._kv2_read_sync, path)

    async def list_keys(self, path: str):
        return await asyncio.to_thread(self._list_keys_sync, path)

    # -----------------------------
    # BREAKGLASS (async)
    # -----------------------------
    async def list_devices(self):
        return await self.list_keys("breakglass")

    async def generate_breakglass(self, device_name: str):
        username = f"bg-{device_name}"
        password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))

        path = f"breakglass/{device_name}"
        payload = {"username": username, "password": password}

        await self.kv2_write(path, payload)
        return payload

    async def get_breakglass_users(self):
        devices = await self.list_keys("breakglass")

        users = []
        for device in devices:
            data = await self.kv2_read(f"breakglass/{device}")
            if data:
                users.append({
                    "device": device,
                    "username": data.get("username"),
                })

        return users

    async def get_breakglass_secret(self, device_name: str):
        path = f"breakglass/{device_name}"
        return await self.kv2_read(path)

    async def get_breakglass_accounts(self):
        devices = await self.list_keys("breakglass")

        accounts = []
        for device in devices:
            data = await self.kv2_read(f"breakglass/{device}")
            if data:
                accounts.append({
                    "device": device,
                    "username": data.get("username"),
                    "password": data.get("password"),
                })

        return accounts

    # -----------------------------
    # OTP (async)
    # -----------------------------
    async def save_otp_secret(self, username: str, otp_seed: str):
        path = f"otp/{username}"
        payload = {
            "otp_seed": otp_seed,
            "username": username,
            "issuer": "BreakglassApp",
        }
        await self.kv2_write(path, payload)

    async def get_otp_secret(self, username: str):
        path = f"otp/{username}"
        return await self.kv2_read(path)

    async def list_otp_accounts(self):
        return await self.list_keys("otp")

    async def save_pending_seed(self, username: str, otp_seed: str):
        path = f"otp_pending/{username}"
        payload = {"otp_seed": otp_seed}
        await self.kv2_write(path, payload)

    async def get_pending_seed(self, username: str):
        path = f"otp_pending/{username}"
        return await self.kv2_read(path)

    async def delete_pending_seed(self, username: str):
        path = f"otp_pending/{username}"
        await self.kv2_write(path, {})  # or kv2_delete if you have it


# class VaultClient:
#     def __init__(self, app, tenant="NCP"):
#         self.app = app
#         self.tenant = tenant

#         tenant_cfg = app.state.config["tenants"][self.tenant]
#         vault_cfg = tenant_cfg["vault"]

#         self.vault_addr = vault_cfg["vault_address"]
#         self.role_id_file = vault_cfg["approle_id_file"]
#         self.secret_id_file = vault_cfg["secret_id_file"]

#         # Load AppRole credentials
#         role_id = self._load_file(self.role_id_file)
#         secret_id = self._load_file(self.secret_id_file)

#         # Create hvac client
#         self.client = hvac.Client(
#             url=self.vault_addr,
#             verify=False,
#         )

#         # Login using AppRole
#         self.client.auth.approle.login(
#             role_id=role_id,
#             secret_id=secret_id,
#         )

#     # -----------------------------
#     # Internal helpers
#     # -----------------------------
#     def _load_file(self, path: str) -> str:
#         if not os.path.isfile(path):
#             raise FileNotFoundError(f"Credential file not found: {path}")
#         with open(path, "r") as f:
#             return f.read().strip()

#     # -----------------------------
#     # KV v2 helpers (correct mount)
#     # -----------------------------
#     def _kv2_write(self, path: str, data: dict):
#         """Write KV v2 secret."""
#         self.client.secrets.kv.v2.create_or_update_secret(
#             mount_point=self.tenant,   # NCP
#             path=path,                 # breakglass/<device>
#             secret=data,
#         )
#         return True

#     def _kv2_read(self, path: str) -> dict | None:
#         """Read KV v2 secret."""
#         try:
#             resp = self.client.secrets.kv.v2.read_secret(
#                 mount_point=self.tenant,
#                 path=path,
#             )
#             return resp["data"]["data"]
#         except hvac.exceptions.InvalidPath:
#             return None

#     def list_keys(self, path: str) -> list[str]:
#         """List KV v2 keys under a path."""
#         try:
#             resp = self.client.secrets.kv.v2.list_secrets(
#                 mount_point=self.tenant,
#                 path=path,
#             )
#             return resp["data"]["keys"]
#         except hvac.exceptions.InvalidPath:
#             return []

#     # -----------------------------
#     # BREAKGLASS
#     # -----------------------------
#     def list_devices(self) -> list[str]:
#         """List all breakglass devices."""
#         return self.list_keys("breakglass")

#     def generate_breakglass(self, device_name: str):
#         """Generate username + password for a device."""
#         username = f"bg-{device_name}"
#         password = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(20))

#         path = f"breakglass/{device_name}"
#         payload = {"username": username, "password": password}

#         self._kv2_write(path, payload)
#         return payload

#     def get_breakglass_secret(self, device_name: str) -> dict | None:
#         path = f"breakglass/{device_name}"
#         return self._kv2_read(path)

#     def get_breakglass_accounts(self):
#         """List all breakglass accounts under breakglass/*"""
#         devices = self.list_keys("breakglass")

#         accounts = []
#         for device in devices:
#             data = self._kv2_read(f"breakglass/{device}")
#             if data:
#                 accounts.append({
#                     "device": device,
#                     "username": data.get("username"),
#                     "password": data.get("password"),
#                 })

#         return accounts

#     # -----------------------------
#     # OTP
#     # -----------------------------
#     def save_otp_secret(self, username: str, otp_seed: str):
#         path = f"otp/{username}"
#         payload = {
#             "otp_seed": otp_seed,
#             "username": username,
#             "issuer": "BreakglassApp",
#         }
#         self._kv2_write(path, payload)

#     def get_otp_secret(self, username: str) -> dict | None:
#         path = f"otp/{username}"
#         return self._kv2_read(path)

#     def list_otp_accounts(self) -> list[str]:
#         return self.list_keys("otp")


# -----------------------------
# MAIN TEST FUNCTION
# -----------------------------
async def main():
    print("=== VaultClient Test Harness ===")

    app = FakeApp()
    vault = VaultClient(app, tenant="NCP")

    # 3. Test reading OTP secret
    print("\n[TEST] Reading OTP secret...")
    otp_data = await vault.get_otp_secret("tuan")
    print("OTP Data:", otp_data)

    # 2. Test writing OTP secret
    # print("\n[TEST] Writing OTP secret...")
    # await vault.save_otp_secret("tuan", "TEST-OTP-SEED-123456")

    # # 3. Test reading OTP secret
    # print("\n[TEST] Reading OTP secret...")
    # otp_data = await vault.get_otp_secret("tuan")
    # print("OTP Data:", otp_data)

    # # 4. Test reading breakglass secret
    # print("\n[TEST] Reading breakglass secret...")
    # bg_data = await vault.get_breakglass_secret("router01")
    # print("Breakglass Data:", bg_data)

async def main2():
    app = FakeApp()
    vault = VaultClient(app)

    # print("\n=== TEST: List devices ===")
    # devices = vault.list_devices()
    # print("Devices:", devices)

    print("\n=== TEST: Get breakglass for device routerA ===")
    bg = await vault.get_breakglass_accounts()
    print("Breakglass:", bg)

    # print("\n=== TEST: Generate breakglass for device router01 ===")
    # bg = await vault.generate_breakglass("RouterA")
    # print("Breakglass:", bg)

    # print("\n=== TEST: List OTP accounts ===")
    # accounts = await vault.list_otp_accounts()
    # print("OTP accounts:", accounts)

    # print("\n=== TEST: Generate OTP for user tuan ===")
    # otp = await vault.generate_otp("tuan")
    # print("OTP seed:", otp)

    # print("\n=== TEST: Read OTP back ===")
    # otp_data = await vault.get_otp_secret("tuan")
    # print("OTP data:", otp_data)



# -----------------------------
# Run main()
# -----------------------------
if __name__ == "__main__":
    asyncio.run(main2())
