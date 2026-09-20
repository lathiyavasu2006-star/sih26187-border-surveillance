"""Password hashing and secret-masking helpers for the database layer."""
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from passlib.context import CryptContext

from backend.core.config import settings

MASK = "***"

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.BCRYPT_ROUNDS,
)

# Keys whose values must never leave the backend in clear text.
SENSITIVE_KEY_PATTERN = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|api[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_BYTES = 72  # bcrypt silently truncates beyond 72 bytes


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(plain_password, password_hash)
    except (ValueError, TypeError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return pwd_context.needs_update(password_hash)


# Pre-computed hash used to equalise timing when a username does not exist.
DUMMY_PASSWORD_HASH = pwd_context.hash("sih26187-timing-equaliser-not-a-real-password")


def validate_password_strength(password: str) -> str:
    """Raise ValueError unless the password meets the government password policy."""
    errors = []
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f"at least {PASSWORD_MIN_LENGTH} characters")
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        errors.append(f"at most {PASSWORD_MAX_BYTES} bytes")
    if not re.search(r"[A-Z]", password):
        errors.append("an uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("a lowercase letter")
    if not re.search(r"\d", password):
        errors.append("a digit")
    if not re.search(r"[^A-Za-z0-9]", password):
        errors.append("a special character")
    if re.search(r"\s", password):
        errors.append("no whitespace")
    if errors:
        raise ValueError("Password must contain " + ", ".join(errors))
    return password


def mask_url_credentials(url: str) -> str:
    """rtsp://user:pass@host/stream -> rtsp://user:***@host/stream"""
    if not isinstance(url, str) or "@" not in url or "://" not in url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.password is None:
        return url
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username}:{MASK}@{host}" if parts.username else f"{MASK}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def mask_sensitive(value: Any) -> Any:
    """Recursively replace secret values in dicts/lists with '***'."""
    if isinstance(value, dict):
        masked = {}
        for key, item in value.items():
            if isinstance(key, str) and SENSITIVE_KEY_PATTERN.search(key) and item not in (None, ""):
                masked[key] = MASK
            else:
                masked[key] = mask_sensitive(item)
        return masked
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return mask_url_credentials(value)
    return value
