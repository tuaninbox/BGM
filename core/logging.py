import logging
import json
import sys

class JsonFormatter(logging.Formatter):
    def format(self, record):
        data = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra", None)
        if extra:
            data.update(extra)
        return json.dumps(data)

logger = logging.getLogger("breakglass")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logger.setLevel(logging.INFO)
logger.addHandler(handler)

def log_event(event: str, **extra):
    logger.info(event, extra={"event": event, **extra})
