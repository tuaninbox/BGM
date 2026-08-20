from datetime import datetime
from zoneinfo import ZoneInfo

perth_tz = ZoneInfo("Australia/Perth")

def to_local_time(dt_str):
    if not dt_str:
        return None
    dt = datetime.fromisoformat(dt_str)          # aware UTC datetime
    return dt.astimezone(perth_tz).isoformat()   # local Perth ISO8601
