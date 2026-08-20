def has_permission(user_role: str, permission: str, roles: dict) -> bool:
    return permission in roles.get(user_role, [])