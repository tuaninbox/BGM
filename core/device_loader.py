import csv,os
from typing import List, Dict, Any
from core.nagios import get_hosts_from_all_hostgroups, export_hosts_to_csv


def load_devices_from_file(path: str) -> List[Dict]:
    devices = []
    
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            devices.append({
                "id": row.get("Host") or row.get("IP"),
                "name": row.get("Host").lower(),
                "ip": row.get("IP"),
                "port": row.get("Port"),
                "location": row.get("Location"),
                "group": row.get("Group"),
                "os": row.get("OS"),
            })
    return devices


async def load_devices(config: Dict[str, Any], tenant: str) -> List[Dict]:
    """
    Load devices for a specific tenant.
    """

    # 1. Validate tenant
    if "tenants" not in config:
        raise ValueError("Config missing 'tenants' section")

    if tenant not in config["tenants"]:
        raise ValueError(f"Tenant '{tenant}' not found in config")

    tenant_cfg = config["tenants"][tenant]

    if "devices" not in tenant_cfg:
        raise ValueError(f"Tenant '{tenant}' missing devices config")

    devices_cfg = tenant_cfg["devices"]
    source = devices_cfg["source"]

    # ------------------------------------------------------------
    # Load from file
    # ------------------------------------------------------------
    if source == "file":
        inventory_root = "inventory"
        path = os.path.join(inventory_root,tenant,devices_cfg["inventory_file"])
        
        return load_devices_from_file(path)

    # ------------------------------------------------------------
    # Load from Nagios
    # ------------------------------------------------------------
    elif source == "nagios":
        nagios_cfg = devices_cfg["nagios"]

        # 1. Fetch devices from Nagios
        devices = await get_hosts_from_all_hostgroups({
            "nagios": nagios_cfg
        })

        # 2. Save devices to inventory_file
        path = devices_cfg["inventory_file"]
        export_hosts_to_csv(devices, path)

        # 3. Reload from file
        return load_devices_from_file(path)

    # ------------------------------------------------------------
    # Unknown source
    # ------------------------------------------------------------
    else:
        raise ValueError(f"Unknown device source: {source}")


# async def load_devices(config: Dict[str, Any]) -> List[Dict]:
#     """
#     Load devices either from Nagios or from CSV file.
#     If source is Nagios:
#         - Fetch devices from Nagios
#         - Save them to the same file_path
#         - Reload from file to return final list
#     """

#     source = config["devices"]["source"]
#     # ------------------------------------------------------------
#     # Load from file
#     # ------------------------------------------------------------
#     if source == "file":
#         path = config["devices"]["file_path"]
#         return load_devices_from_file(path)

#     # ------------------------------------------------------------
#     # Load from Nagios
#     # ------------------------------------------------------------
#     elif source == "nagios":
#         nagios_cfg = config["devices"]["nagios"]

#         # 1. Fetch devices from Nagios
#         devices = await get_hosts_from_all_hostgroups(config)

#         # 2. Save devices to the same file_path
#         path = config["devices"]["file_path"]
#         export_hosts_to_csv(devices, path)

#         # 3. Reload from file (ensures consistent format)
#         return load_devices_from_file(path)

#     # ------------------------------------------------------------
#     # Unknown source
#     # ------------------------------------------------------------
#     else:
#         raise ValueError(f"Unknown device source: {source}")


async def main():
    from core.config_loader import load_config

    config = load_config()
    print("Loaded config:", config)

    devices = await load_devices(config,"NCP")
    print("Devices from inventory:", devices)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())