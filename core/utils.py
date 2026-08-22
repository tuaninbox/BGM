from datetime import datetime
from zoneinfo import ZoneInfo

perth_tz = ZoneInfo("Australia/Perth")

def to_local_time(dt_str):
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str)          # aware UTC datetime
    return dt.astimezone(perth_tz).isoformat()   # local Perth ISO8601


def parse_iso8601(s: str):
    """
    Safely parse ISO8601 timestamps stored as VARCHAR in DB.
    Example: 2026-08-22T19:30:16.355518+00:00
    """
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None