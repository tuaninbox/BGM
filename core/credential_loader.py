import os
import yaml
import configparser
from pathlib import Path
from typing import Dict, Any

def load_credentials(config: Dict[str, Any], tenant: str) -> Dict[str, Any]:
    """
    Load tenant-specific credentials with correct priority:

    1. ENV variables (highest priority)
    2. Credential file (YAML or INI)
    3. Normalize keys:
        {
            "tacacs": {"username": "...", "password": "..."},
            "nagios": {"nagios_host": "...", "nagios_apikey": "..."}
        }
    """

    # -----------------------------
    # STEP 1 — Prepare empty result
    # -----------------------------
    normalized: Dict[str, Dict[str, Any]] = {
        "tacacs": {},
        "nagios": {},
    }

    # -----------------------------
    # STEP 2 — Load ENV first
    # -----------------------------
    # TACACS
    env_tacacs_user = os.getenv("DEVICE_USERNAME")
    env_tacacs_pass = os.getenv("DEVICE_PASSWORD")

    if env_tacacs_user:
        normalized["tacacs"]["username"] = env_tacacs_user
    if env_tacacs_pass:
        normalized["tacacs"]["password"] = env_tacacs_pass

    # NAGIOS
    env_nagios_host = os.getenv("NAGIOS_HOST")
    env_nagios_api = os.getenv("NAGIOS_APIKEY")

    if env_nagios_host:
        normalized["nagios"]["nagios_host"] = env_nagios_host
    if env_nagios_api:
        normalized["nagios"]["nagios_apikey"] = env_nagios_api

    # -----------------------------
    # STEP 3 — Load tenant credential file
    # -----------------------------
    if "tenants" not in config:
        raise ValueError("Config missing 'tenants' section")

    if tenant not in config["tenants"]:
        raise ValueError(f"Tenant '{tenant}' not found in config")

    tenant_cfg = config["tenants"][tenant]
    cred_section = tenant_cfg.get("credentials", {})

    cred_file = cred_section.get("credential_file")
    if not cred_file:
        raise ValueError(f"credentials.credential_file missing for tenant '{tenant}'")

    possible_paths = [
        Path(f"{cred_file}.yaml"),
        Path(f"{cred_file}.yml"),
        Path(f"{cred_file}.ini"),
        Path(f"{cred_file}.cfg"),
        Path(cred_file)
    ]

    cred_path = next((p for p in possible_paths if p.exists()), None)
    if not cred_path:
        raise FileNotFoundError(f"Credential file not found: {cred_file}")

    ext = cred_path.suffix.lower()

    # -----------------------------
    # STEP 4 — YAML loader
    # -----------------------------
    if ext in [".yaml", ".yml"]:
        with open(cred_path, "r") as f:
            raw = yaml.safe_load(f) or {}

        for section, items in raw.items():
            if isinstance(items, dict):
                for key, value in items.items():
                    if key not in normalized.get(section, {}):
                        normalized.setdefault(section, {})[key] = value

            elif isinstance(items, list):
                merged = {}
                for entry in items:
                    if isinstance(entry, dict):
                        merged.update(entry)

                for key, value in merged.items():
                    if key not in normalized.get(section, {}):
                        normalized.setdefault(section, {})[key] = value

    # -----------------------------
    # STEP 5 — INI loader
    # -----------------------------
    else:
        parser = configparser.ConfigParser()
        parser.read(cred_path)

        for section in parser.sections():
            for key, value in parser.items(section):
                if key not in normalized.get(section, {}):
                    normalized.setdefault(section, {})[key] = value

    # -----------------------------
    # STEP 6 — Normalize key names
    # -----------------------------
    nagios = normalized["nagios"]

    # Accept nagios_api or nagios_apikey
    if "nagios_api" in nagios:
        nagios["nagios_apikey"] = nagios["nagios_api"]

    nagios.setdefault("nagios_host", "")
    nagios.setdefault("nagios_apikey", "")

    tacacs = normalized["tacacs"]
    tacacs.setdefault("username", "")
    tacacs.setdefault("password", "")

    return normalized


# def load_credentials(config: Dict[str, Any]) -> Dict[str, Any]:
#     """
#     Load credentials with correct priority:

#     1. ENV variables (highest priority)
#         DEVICE_USERNAME → tacacs.username
#         DEVICE_PASSWORD → tacacs.password
#         NAGIOS_HOST     → nagios.nagios_host
#         NAGIOS_APIKEY   → nagios.nagios_apikey

#     2. Credential file (YAML or INI) fallback

#     3. Normalize keys so final structure is:

#         {
#             "tacacs": {"username": "...", "password": "..."},
#             "nagios": {"nagios_host": "...", "nagios_apikey": "..."}
#         }
#     """

#     # -----------------------------
#     # STEP 1 — Prepare empty result
#     # -----------------------------
#     normalized: Dict[str, Dict[str, Any]] = {
#         "tacacs": {},
#         "nagios": {},
#     }

#     # -----------------------------
#     # STEP 2 — Load ENV first
#     # -----------------------------
#     # TACACS
#     env_tacacs_user = os.getenv("DEVICE_USERNAME")
#     env_tacacs_pass = os.getenv("DEVICE_PASSWORD")

#     if env_tacacs_user:
#         normalized["tacacs"]["username"] = env_tacacs_user
#     if env_tacacs_pass:
#         normalized["tacacs"]["password"] = env_tacacs_pass

#     # NAGIOS
#     env_nagios_host = os.getenv("NAGIOS_HOST")
#     env_nagios_api = os.getenv("NAGIOS_APIKEY")

#     if env_nagios_host:
#         normalized["nagios"]["nagios_host"] = env_nagios_host
#     if env_nagios_api:
#         normalized["nagios"]["nagios_apikey"] = env_nagios_api

#     # -----------------------------
#     # STEP 3 — Load file ONLY IF ENV missing
#     # -----------------------------
#     cred_section = config.get("credentials", {})
#     cred_file = cred_section.get("file")

#     if not cred_file:
#         raise ValueError("credentials.file is missing in config/config.yaml")

#     possible_paths = [
#         Path(f"{cred_file}.yaml"),
#         Path(f"{cred_file}.yml"),
#         Path(f"{cred_file}.ini"),
#         Path(f"{cred_file}.cfg"),
#         Path(cred_file)
#     ]

#     cred_path = next((p for p in possible_paths if p.exists()), None)
#     if not cred_path:
#         raise FileNotFoundError(f"Credential file not found: {cred_file}")

#     ext = cred_path.suffix.lower()

#     # YAML loader
#     if ext in [".yaml", ".yml"]:
#         with open(cred_path, "r") as f:
#             raw = yaml.safe_load(f) or {}

#         for section, items in raw.items():
#             if isinstance(items, dict):
#                 for key, value in items.items():
#                     # Only fill missing ENV values
#                     if key not in normalized.get(section, {}):
#                         normalized.setdefault(section, {})[key] = value

#             elif isinstance(items, list):
#                 merged = {}
#                 for entry in items:
#                     if isinstance(entry, dict):
#                         merged.update(entry)

#                 for key, value in merged.items():
#                     if key not in normalized.get(section, {}):
#                         normalized.setdefault(section, {})[key] = value

#     # INI loader
#     else:
#         parser = configparser.ConfigParser()
#         parser.read(cred_path)

#         for section in parser.sections():
#             for key, value in parser.items(section):
#                 if key not in normalized.get(section, {}):
#                     normalized.setdefault(section, {})[key] = value

#     # -----------------------------
#     # STEP 4 — Normalize key names
#     # -----------------------------
#     # Accept nagios_api or nagios_apikey
#     nagios = normalized["nagios"]

#     if "nagios_api" in nagios:
#         nagios["nagios_apikey"] = nagios["nagios_api"]

#     # Ensure keys exist
#     nagios.setdefault("nagios_host", "")
#     nagios.setdefault("nagios_apikey", "")

#     tacacs = normalized["tacacs"]
#     tacacs.setdefault("username", "")
#     tacacs.setdefault("password", "")

#     return normalized


def main():
    from core.config_loader import load_config

    print("Loading config...")
    config = load_config()
    print("Loaded config")

    print("\nLoading credentials (ENV → file fallback)...")
    creds = load_credentials(config)
    print("Loaded credentials")

    # Merge credentials into config
    if "credentials" in config:
        config["credentials"]["loaded"] = creds

    print("\nCredential merged to config")

if __name__ == "__main__":
    main()


# Usage:
# from core.config_loader import load_config
# from core.credential_loader import load_credentials

# config = load_config()
# creds = load_credentials(config)

# tacacs_user = creds["tacacs"]["username"]
# tacacs_pass = creds["tacacs"]["password"]

# nagios_host = creds["nagios"]["apihost"]
# nagios_key  = creds["nagios"]["apikey"]
