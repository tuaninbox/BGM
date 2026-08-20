import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, List


def load_device_group_mapping(path: str = "config/device_group_map.yaml") -> List[Dict[str, str]]:
    """
    Load YAML mapping file containing regex → zone rules.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Device zone mapping file not found: {path}")

    with open(p, "r") as f:
        data = yaml.safe_load(f) or {}

    return data.get("mapping", [])


def map_device_name_to_group(device_name: str, mapping: List[Dict[str, str]]) -> Optional[str]:
    """
    Match device name against regex patterns from mapping file.
    Returns zone or None if no match.
    """
    if not device_name:
        return None

    name = device_name.lower()

    for rule in mapping:
        pattern = rule.get("pattern")
        zone = rule.get("zone")

        if not pattern or not zone:
            continue

        if re.match(pattern, name):
            return zone

    return None
