import os
import yaml
from pathlib import Path
from typing import Any, Dict


def load_config(path: str = "config/config.yaml") -> Dict[str, Any]:
    """
    Load YAML config and allow environment variable overrides.
    Supports nested sections using ENV variables like:
    DEVICES_SOURCE, DEVICES_FILE_PATH, NAGIOS_HOST, NAGIOS_API
    """
    if Path(path).exists():
        with open(path, "r") as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}

    # Apply environment overrides
    for section, values in config.items():
        if isinstance(values, dict):
            for key, value in values.items():
                env_key = f"{section.upper()}_{key.upper()}"
                if env_key in os.environ:
                    config[section][key] = os.environ[env_key]

    return config

def main():
    config = load_config()
    print("Loaded configuration:")
    print(config)


if __name__ == "__main__":
    main()
