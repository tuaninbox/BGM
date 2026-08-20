import unittest
from unittest.mock import MagicMock, patch
from core.vault import VaultClient, FakeApp


class TestVaultClient(unittest.TestCase):

    def setUp(self):
        self.app = FakeApp()

        # Patch hvac.Client so no real Vault calls happen
        patcher = patch("core.vault.hvac.Client")
        self.addCleanup(patcher.stop)
        self.mock_hvac = patcher.start()

        # Create fake hvac client instance
        self.mock_client = MagicMock()
        self.mock_hvac.return_value = self.mock_client

        # Instantiate VaultClient
        self.vault = VaultClient(self.app, tenant="NCP")

    # ---------------------------------------------------------
    # KV WRITE
    # ---------------------------------------------------------
    def test_kv_write(self):
        self.vault._kv2_write("breakglass/router01", {"username": "bg-router01"})
        self.mock_client.secrets.kv.v2.create_or_update_secret.assert_called_with(
            mount_point="NCP",
            path="breakglass/router01",
            secret={"username": "bg-router01"},
        )

    # ---------------------------------------------------------
    # KV READ
    # ---------------------------------------------------------
    def test_kv_read(self):
        self.mock_client.secrets.kv.v2.read_secret.return_value = {
            "data": {"data": {"username": "bg-router01"}}
        }

        result = self.vault._kv2_read("breakglass/router01")
        self.assertEqual(result["username"], "bg-router01")

        self.mock_client.secrets.kv.v2.read_secret.assert_called_with(
            mount_point="NCP",
            path="breakglass/router01",
        )

    # ---------------------------------------------------------
    # LIST KEYS
    # ---------------------------------------------------------
    def test_list_keys(self):
        self.mock_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["RouterA", "RouterB"]}
        }

        keys = self.vault.list_keys("breakglass")
        self.assertEqual(keys, ["RouterA", "RouterB"])

        self.mock_client.secrets.kv.v2.list_secrets.assert_called_with(
            mount_point="NCP",
            path="breakglass",
        )

    # ---------------------------------------------------------
    # BREAKGLASS: list_devices()
    # ---------------------------------------------------------
    def test_list_devices(self):
        self.mock_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["RouterA"]}
        }

        devices = self.vault.list_devices()
        self.assertEqual(devices, ["RouterA"])

    # ---------------------------------------------------------
    # BREAKGLASS: generate_breakglass()
    # ---------------------------------------------------------
    def test_generate_breakglass(self):
        payload = self.vault.generate_breakglass("RouterA")

        self.assertIn("username", payload)
        self.assertIn("password", payload)
        self.assertTrue(payload["username"].startswith("bg-RouterA"))

        self.mock_client.secrets.kv.v2.create_or_update_secret.assert_called()

    # ---------------------------------------------------------
    # BREAKGLASS: get_breakglass_secret()
    # ---------------------------------------------------------
    def test_get_breakglass_secret(self):
        self.mock_client.secrets.kv.v2.read_secret.return_value = {
            "data": {"data": {"username": "bg-RouterA", "password": "123"}}
        }

        result = self.vault.get_breakglass_secret("RouterA")
        self.assertEqual(result["username"], "bg-RouterA")

    # ---------------------------------------------------------
    # OTP: save_otp_secret()
    # ---------------------------------------------------------
    def test_save_otp_secret(self):
        self.vault.save_otp_secret("tuan", "OTPSEED123")

        self.mock_client.secrets.kv.v2.create_or_update_secret.assert_called_with(
            mount_point="NCP",
            path="otp/tuan",
            secret={
                "otp_seed": "OTPSEED123",
                "username": "tuan",
                "issuer": "BreakglassApp",
            },
        )

    # ---------------------------------------------------------
    # OTP: get_otp_secret()
    # ---------------------------------------------------------
    def test_get_otp_secret(self):
        self.mock_client.secrets.kv.v2.read_secret.return_value = {
            "data": {"data": {"otp_seed": "XYZ", "username": "tuan"}}
        }

        result = self.vault.get_otp_secret("tuan")
        self.assertEqual(result["otp_seed"], "XYZ")

    # ---------------------------------------------------------
    # OTP: list_otp_accounts()
    # ---------------------------------------------------------
    def test_list_otp_accounts(self):
        self.mock_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["tuan", "admin"]}
        }

        accounts = self.vault.list_otp_accounts()
        self.assertEqual(accounts, ["tuan", "admin"])


if __name__ == "__main__":
    unittest.main()
