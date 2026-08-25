import httpx
from typing import List, Dict, Any
from core.device_group_mapper import load_device_group_mapping, map_device_name_to_group

# Load mapping once
group_mapping = load_device_group_mapping()


def normalize_netbox_host(netbox_host: str) -> str:
    """
    Ensure netbox_host always has http/https.
    If missing, default to https:// for safety.
    """
    if not netbox_host:
        return ""

    netbox_host = netbox_host.strip()

    if netbox_host.startswith(("http://", "https://")):
        return netbox_host

    return f"https://{netbox_host}"


def derive_os_from_netbox(device: Dict[str, Any]) -> str:
    """
    Derive OS from NetBox device fields.
    Adjust logic based on your naming conventions.
    """
    name = (device.get("name") or "").lower()
    platform = (device.get("platform", {}).get("name") or "").lower()

    if "ios" in name or "ios" in platform:
        return "IOS"
    if "nxos" in name or "nxos" in platform:
        return "NXOS"

    return "Unknown"


# ------------------------------------------------------------
# STEP 1 — Get all devices from NetBox
# ------------------------------------------------------------
async def get_netbox_devices(
    netbox_host: str,
    netbox_token: str,
    verify_ssl: bool = False,
) -> List[Dict]:

    netbox_host = normalize_netbox_host(netbox_host)

    url = f"{netbox_host}/api/dcim/devices/"
    headers = {"Authorization": f"Token {netbox_token}"}

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    # NetBox returns: {"count": X, "results": [...]}
    return data.get("results", [])


# ------------------------------------------------------------
# STEP 2 — Get device details (interfaces, IPs, etc.)
# ------------------------------------------------------------
async def get_netbox_device_details(
    netbox_host: str,
    netbox_token: str,
    device: Dict[str, Any],
    verify_ssl: bool = False,
) -> Dict:

    netbox_host = normalize_netbox_host(netbox_host)
    device_id = device.get("id")

    headers = {"Authorization": f"Token {netbox_token}"}

    # Get primary IP
    primary_ip = ""
    if device.get("primary_ip"):
        primary_ip = device["primary_ip"].get("address", "").split("/")[0]

    # Map group using your existing mapper
    group = map_device_name_to_group(device.get("name", ""), group_mapping)

    return {
        "id": device.get("id"),
        "name": device.get("name"),
        "ip": primary_ip,
        "port": "",  # NetBox doesn't store port like Nagios
        "location": device.get("site", {}).get("name", ""),
        "group": group,
        "os": derive_os_from_netbox(device),
    }


# ------------------------------------------------------------
# STEP 3 — Combine both steps
# ------------------------------------------------------------
async def get_devices_from_netbox(config: Dict[str, Any]) -> List[Dict]:
    netbox_cfg = config["devices"]["netbox"]

    netbox_host = normalize_netbox_host(netbox_cfg.get("netbox_host", ""))
    netbox_token = netbox_cfg.get("netbox_token", "")

    if not netbox_host:
        raise ValueError("NetBox host is missing or empty.")

    if not netbox_token:
        raise ValueError("NetBox API token is missing or empty.")

    # Step 1: get all devices
    devices = await get_netbox_devices(
        netbox_host=netbox_host,
        netbox_token=netbox_token,
        verify_ssl=False,
    )

    # Step 2: get details for each device
    all_hosts: List[Dict] = []

    for d in devices:
        details = await get_netbox_device_details(
            netbox_host=netbox_host,
            netbox_token=netbox_token,
            device=d,
            verify_ssl=False,
        )
        all_hosts.append(details)

    return all_hosts


# ------------------------------------------------------------
# CSV Export (same format as Nagios)
# ------------------------------------------------------------
def export_netbox_to_csv(hosts: List[Dict], output_file: str = "netbox_hosts.csv"):
    import csv

    fields = ["Host", "IP", "Port", "Location", "Group", "OS"]

    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)

        for h in hosts:
            writer.writerow([
                h.get("name", ""),
                h.get("ip", ""),
                h.get("port", ""),
                h.get("location", ""),
                h.get("group", ""),
                h.get("os", ""),
            ])

    return output_file
