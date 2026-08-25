from datetime import datetime, timedelta, timezone
from jose import jwt
from passlib.context import CryptContext
from time import time
from typing import Tuple
from core.settings import settings
import logging, re
import secrets
import string

# In-memory brute-force tracking
FAILED_ATTEMPTS = {}  # key: (ip, username), value: [timestamps]
LOCKOUTS = {}          # key: (ip, username), value: lockout_until_timestamp

# Configurable limits
MAX_ATTEMPTS = 5          # number of failures allowed
WINDOW_SECONDS = 300      # time window (5 minutes)
LOCKOUT_SECONDS = 600     # 10-minute lockout 

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
# Logger
logger = logging.getLogger("security.bruteforce")
logger.setLevel(logging.INFO)


def generate_api_token():
    raw = secrets.token_urlsafe(32)  # 32 bytes → ~43 chars
    return f"rot_{raw}"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)

def create_access_token(data: dict, expires_minutes: int | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_exp_minutes
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)

def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])

class PasswordValidationError(Exception):
    pass


class PasswordValidationError(Exception):
    pass


class UsernameValidationError(Exception):
    pass


def validate_username(username: str):
    """
    Enforces:
    - Minimum length 5
    - Only letters, digits, underscore
    """
    if not username or len(username) < 5:
        raise UsernameValidationError(
            "Username must be at least 5 characters long."
        )

    # Allowed: a-z, A-Z, 0-9, _
    if not re.match(r"^[A-Za-z0-9_]+$", username):
        raise UsernameValidationError(
            "Username can only contain letters, digits, and underscore."
        )

def validate_password_complexity(password: str):
    """
    Enforces:
    - Minimum length 10
    - Must contain uppercase, lowercase, digit, special char
    - Raises PasswordValidationError with a unified message
    """

    length_ok = len(password) >= 10
    has_upper = re.search(r"[A-Z]", password)
    has_lower = re.search(r"[a-z]", password)
    has_digit = re.search(r"[0-9]", password)
    has_special = re.search(r"[^A-Za-z0-9]", password)

    if not (length_ok and has_upper and has_lower and has_digit and has_special):
        raise PasswordValidationError(
            "Password must be at least 10 characters long and contain all 4 character groups: "
            "uppercase, lowercase, digit, and special character."
        )


# ---------------------------------------------------------
# Email Alert Wrapper (uses core.email.send_email)
# ---------------------------------------------------------
async def send_alert_email(ip: str, username: str, attempts: int):
    """Send brute-force alert using the shared email helper."""

    subject = f"[SECURITY ALERT] Brute-force detected for user {username}"

    body = f"""
Brute-force activity detected:

User: {username}
IP Address: {ip}
Failed Attempts: {attempts}
Time Window: {WINDOW_SECONDS} seconds

Recommended Action:
- Check logs
- Consider blocking the IP
- Investigate suspicious activity
"""

    try:
        await send_email(ALERT_EMAIL_TO, subject, body)
        logger.info(f"Sent brute-force alert email for {username} from {ip}")
    except Exception as e:
        logger.error(f"Failed to send brute-force alert email: {e}")


# ---------------------------------------------------------
# Brute-force Logic
# ---------------------------------------------------------
def _key(ip: str, username: str) -> Tuple[str, str]:
    return (ip, username)


def is_locked_out(ip: str, username: str) -> bool:
    """Check if this IP+username is currently locked out."""
    key = _key(ip, username)
    lockout_until = LOCKOUTS.get(key)

    if lockout_until and time() < lockout_until:
        return True

    # Lockout expired → remove it
    if lockout_until:
        LOCKOUTS.pop(key, None)

    return False


def record_failed_attempt(ip: str, username: str):
    """Record a failed login attempt."""
    now = time()
    key = _key(ip, username)

    attempts = FAILED_ATTEMPTS.get(key, [])
    attempts = [t for t in attempts if now - t < WINDOW_SECONDS]
    attempts.append(now)

    FAILED_ATTEMPTS[key] = attempts

    logger.warning(
        f"Failed login attempt for user={username} from IP={ip} "
        f"({len(attempts)} attempts)"
    )


async def too_many_attempts(ip: str, username: str) -> bool:
    """Check if this IP+username has exceeded the allowed attempts."""
    now = time()
    key = _key(ip, username)

    # Check lockout first
    if is_locked_out(ip, username):
        logger.error(f"LOGIN BLOCKED (LOCKOUT): user={username} from IP={ip}")
        return True

    # Check recent failed attempts
    attempts = FAILED_ATTEMPTS.get(key, [])
    attempts = [t for t in attempts if now - t < WINDOW_SECONDS]
    FAILED_ATTEMPTS[key] = attempts

    if len(attempts) >= MAX_ATTEMPTS:
        # Trigger lockout
        LOCKOUTS[key] = now + LOCKOUT_SECONDS

        logger.error(
            f"BRUTE FORCE DETECTED → LOCKOUT ENABLED: "
            f"user={username} from IP={ip} ({len(attempts)} attempts)"
        )

        await send_alert_email(ip, username, len(attempts))
        return True

    return False


def clear_attempts(ip: str, username: str):
    """Clear failed attempts after successful login."""
    key = _key(ip, username)
    FAILED_ATTEMPTS.pop(key, None)
    LOCKOUTS.pop(key, None)
    logger.info(f"Cleared failed attempts and lockout for user={username} from IP={ip}")


def generate_strong_password():
    upper = secrets.choice(string.ascii_uppercase)
    lower = secrets.choice(string.ascii_lowercase)
    special = secrets.choice("!@#$%^&*()-_=+")
    digit = secrets.choice(string.digits)

    # Add extra random characters for length (optional)
    extra = ''.join(secrets.choice(string.ascii_letters + string.digits + "!@#$%^&*()-_=+") for _ in range(12))

    # Shuffle to avoid predictable order
    password = list(upper + lower + special + digit + extra)
    secrets.SystemRandom().shuffle(password)
    return ''.join(password)

def generate_username(device_name: str):
    base = device_name.lower().replace(" ", "")
    suffix = secrets.randbelow(9999)
    return f"{base}{suffix}"
