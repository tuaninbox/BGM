import httpx
from typing import List, Dict, Any
from core.device_group_mapper import load_device_group_mapping, map_device_name_to_group

# Load mapping once
group_mapping = load_device_group_mapping()


def derive_os_from_group(group: str) -> str:
    group = group.lower()
    if "ios" in group:
        return "IOS"
    if "nxos" in group:
        return "NXOS"
    return "Unknown"


def normalize_nagios_host(nagios_host: str) -> str:
    """
    Ensure nagios_host always has http/https.
    If missing, default to https:// for safety.
    """
    if not nagios_host:
        return ""

    nagios_host = nagios_host.strip()

    if nagios_host.startswith(("http://", "https://")):
        return nagios_host

    return f"https://{nagios_host}"


# ------------------------------------------------------------
# STEP 1 — Get hostgroup members (host_name only)
# ------------------------------------------------------------
async def get_hostgroup_members(
    nagios_host: str,
    nagios_api: str,
    hostgroup: str,
    verify_ssl: bool = False,
) -> List[str]:
    
    nagios_host = normalize_nagios_host(nagios_host)

    url = f"{nagios_host}/nagiosxi/api/v1/objects/hostgroupmembers"
    params = {"apikey": nagios_api, "hostgroup_name": hostgroup}

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

    # hostgroup is ALWAYS a list, even if length = 1
    hostgroup_list = data.get("hostgroup", [])

    if not hostgroup_list:
        return []

    # There is only ONE hostgroup object, but inside a list
    hg = hostgroup_list[0]

    members = hg.get("members", {})
    hosts = members.get("host", [])
    return [h.get("host_name") for h in hosts if h.get("host_name")]


# ------------------------------------------------------------
# STEP 2 — Query host config for each host_name
# ------------------------------------------------------------
async def get_host_details(
    nagios_host: str,
    nagios_api: str,
    host_name: str,
    hg: str,
    verify_ssl: bool = False,
) -> Dict:

    nagios_host = normalize_nagios_host(nagios_host)

    url = f"{nagios_host}/nagiosxi/api/v1/config/host"
    params = {
        "apikey": nagios_api,
        "host_name": host_name,
        "filter": "active",
    }

    async with httpx.AsyncClient(verify=verify_ssl) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()

     # Correct: data is a LIST of host objects
    if not isinstance(data, list) or len(data) == 0:
        # Graceful fallback
        return {
            "id": host_name,
            "name": host_name,
            "ip": "",
            "port": "",
            "location": "",
            "group": map_device_name_to_group(host_name, group_mapping),
            "os": derive_os_from_group(host_name),
        }

    # First host entry
    host = data[0]

    group = map_device_name_to_group(host_name, group_mapping)

    return {
        "id": host.get("host_name") or host.get("address"),
        "name": host.get("host_name"),
        "ip": host.get("address"),
        "port": host.get("port") or "",
        "location": host.get("notes") or "",
        "group": group,
        "os": derive_os_from_group(hg),
    }


# ------------------------------------------------------------
# STEP 3 — Combine both steps for all hostgroups
# ------------------------------------------------------------
async def get_hosts_from_all_hostgroups(config: Dict[str, Any]) -> List[Dict]:
    print(config)
    nagios_cfg = config["devices"]["nagios"]

    nagios_host = normalize_nagios_host(nagios_cfg.get("nagios_host", ""))
    nagios_api = nagios_cfg.get("nagios_apikey", "")
    hostgroups = nagios_cfg.get("hostgroups", [])

    if not nagios_host:
        raise ValueError("Nagios host is missing or empty.")

    if not nagios_api:
        raise ValueError("Nagios API key is missing or empty.")

    all_hosts: List[Dict] = []

    for hg in hostgroups:
        # Step 1: get host names
        hostnames = await get_hostgroup_members(
            nagios_host=nagios_host,
            nagios_api=nagios_api,
            hostgroup=hg,
            verify_ssl=False,
        )

        # Step 2: get details for each host
        for host_name in hostnames:
            details = await get_host_details(
                nagios_host=nagios_host,
                nagios_api=nagios_api,
                host_name=host_name,
                hg=hg,
                verify_ssl=False,
            )
            all_hosts.append(details)

    return all_hosts


# ------------------------------------------------------------
# CSV Export 
# ------------------------------------------------------------
def export_hosts_to_csv(hosts: List[Dict], output_file: str = "nagios_hosts.csv"):
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


async def main():
    from core.config_loader import load_config
    from core.credential_loader import load_credentials
    from core.nagios import get_hosts_from_all_hostgroups
    # Load config
    config = load_config()
    print("Loaded config:")
    print(config)

    # Load credentials (if Nagios credentials stored separately)
    creds = load_credentials(config)
    print("\nLoaded credentials:")
    print(creds)

    # Merge credentials into config if needed
    if ("devices" in config and "nagios" in config["devices"] and "nagios" in creds):
        config["devices"]["nagios"].update(creds["nagios"])

    print("\nFinal Nagios config:")
    print(config["devices"]["nagios"])

    # Test Nagios hostgroup loading
    print("\nFetching hosts from Nagios...")
    hosts = await get_hosts_from_all_hostgroups(config)

    print("\nHosts returned from Nagios:")
    for h in hosts:
        print(h)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
