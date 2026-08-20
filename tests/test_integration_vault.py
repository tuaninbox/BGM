import unittest
import hvac
# from core.vault import VaultClient, FakeApp

from tests.helpers import FakeApp
from core.config_loader import load_config
from core.credential_loader import load_credentials
from core.device_loader import load_devices
from core.role_loader import load_roles
from core.vault import VaultClient


class TestVaultIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Create fake FastAPI app
        cls.app = FakeApp()

        # Load config + credentials exactly like FastAPI
        cls.app.state.config = load_config()
        cls.app.state.credential_loader = load_credentials
        cls.app.state.device_loader = load_devices
        cls.app.state.roles = load_roles()

        # Instantiate VaultClient with real config
        cls.vault = VaultClient(cls.app, tenant="NCP")

        # Raw hvac client for direct Vault checks
        vault_cfg = cls.app.state.config["tenants"]["NCP"]["vault"]
        cls.raw = hvac.Client(url=vault_cfg["vault_address"], verify=False)

        role_id = cls.vault._load_file(vault_cfg["approle_id_file"])
        secret_id = cls.vault._load_file(vault_cfg["secret_id_file"])

        cls.raw.auth.approle.login(role_id=role_id, secret_id=secret_id)

        # ---------------------------------------------------------
        # Pre-populate data
        # ---------------------------------------------------------
        cls.vault.generate_breakglass("RouterA")
        cls.vault.save_otp_secret("tuan", "OTPSEED-XYZ")
    # ---------------------------------------------------------
    # MOUNT VALIDATION
    # ---------------------------------------------------------
    def test_mount_exists(self):
        # Try listing root of mount; if mount doesn't exist, this fails
        resp = self.raw.secrets.kv.v2.list_secrets(
            mount_point="NCP",
            path=""
        )
        self.assertIn("keys", resp["data"])

    # ---------------------------------------------------------
    # BREAKGLASS WRITE
    # ---------------------------------------------------------
    # def test_breakglass_write(self):
    #     payload = self.vault.generate_breakglass("RouterA")
    #     self.assertIn("username", payload)
    #     self.assertIn("password", payload)

    # ---------------------------------------------------------
    # BREAKGLASS READ
    # ---------------------------------------------------------
    def test_breakglass_read(self):
        data = self.vault.get_breakglass_secret("RouterA")
        self.assertIsNotNone(data)
        self.assertIn("username", data)

    # ---------------------------------------------------------
    # BREAKGLASS LIST
    # ---------------------------------------------------------
    def test_breakglass_list(self):
        devices = [d.rstrip("/") for d in self.vault.list_devices()]
        self.assertIn("RouterA", devices)


    # ---------------------------------------------------------
    # OTP WRITE
    # ---------------------------------------------------------
    # def test_otp_write(self):
    #     self.vault.save_otp_secret("tuan", "OTPSEED-XYZ")
    #     data = self.vault.get_otp_secret("tuan")
    #     self.assertEqual(data["otp_seed"], "OTPSEED-XYZ")

    # ---------------------------------------------------------
    # OTP READ
    # ---------------------------------------------------------
    def test_otp_read(self):
        data = self.vault.get_otp_secret("tuan")
        self.assertIsNotNone(data)
        self.assertIn("otp_seed", data)

    # ---------------------------------------------------------
    # OTP LIST
    # ---------------------------------------------------------
    def test_otp_list(self):
        accounts = self.vault.list_otp_accounts()
        self.assertIn("tuan", accounts)


if __name__ == "__main__":
    unittest.main()
