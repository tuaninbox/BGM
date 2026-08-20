import yaml

def load_roles(path: str = "config/roles.yaml") -> dict:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return data["roles"]
