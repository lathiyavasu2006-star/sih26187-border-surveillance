"""Week 2 API-layer settings. Reads the same .env as core/config.py (which is left untouched)."""
from datetime import timedelta, timezone
from functools import lru_cache
from typing import List, Set

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.core.config import ENV_FILE


def _csv(value) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


class APISettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    UPLOAD_MAX_SIZE_MB: int = 500
    ALLOWED_VIDEO_TYPES: str = "mp4,avi,mov,mkv"
    ALLOWED_IMAGE_TYPES: str = "jpg,jpeg,png"
    JWT_BLACKLIST_ENABLED: bool = False
    RATE_LIMIT_PER_MINUTE: int = 100
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"
    CAMERA_MONITOR_ENABLED: bool = True
    LOG_LEVEL: str = "INFO"

    # Stricter limits for credential endpoints (brute-force protection on top of account lockout).
    AUTH_LOGIN_RATE_LIMIT: str = "10/minute"
    AUTH_REFRESH_RATE_LIMIT: str = "30/minute"
    AUTH_SENSITIVE_RATE_LIMIT: str = "20/minute"

    # X-Forwarded-For is honoured only when the direct peer is one of these proxies.
    TRUSTED_PROXIES: str = ""

    # Camera probing: per-probe timeout. Cycle period is derived so detection latency <= threshold.
    CAMERA_PROBE_TIMEOUT_SECONDS: int = 5
    CAMERA_PROBE_CONCURRENCY: int = 32
    SYSTEM_HEALTH_RECORD_INTERVAL_SECONDS: int = 60

    # Roles allowed to push ML frame_data over the WebSocket (the Week 3 pipeline service account).
    WS_INGEST_ROLES: str = "admin,supervisor"
    WS_MAX_FRAME_BYTES: int = 8 * 1024 * 1024

    # Reporting day boundaries ("today") in India Standard Time (UTC+05:30, no DST).
    REPORTING_UTC_OFFSET_MINUTES: int = 330

    @field_validator("LOG_LEVEL")
    @classmethod
    def _log_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("LOG_LEVEL must be CRITICAL, ERROR, WARNING, INFO or DEBUG")
        return value

    @field_validator("UPLOAD_MAX_SIZE_MB", "RATE_LIMIT_PER_MINUTE", "CAMERA_PROBE_TIMEOUT_SECONDS")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("must be positive")
        return value

    @property
    def cors_origins(self) -> List[str]:
        origins = _csv(self.CORS_ORIGINS)
        if "*" in origins:
            raise ValueError("Wildcard CORS origin is not permitted")
        return origins

    @property
    def upload_max_bytes(self) -> int:
        return self.UPLOAD_MAX_SIZE_MB * 1024 * 1024

    @property
    def video_extensions(self) -> Set[str]:
        return {ext.lower().lstrip(".") for ext in _csv(self.ALLOWED_VIDEO_TYPES)}

    @property
    def image_extensions(self) -> Set[str]:
        return {ext.lower().lstrip(".") for ext in _csv(self.ALLOWED_IMAGE_TYPES)}

    @property
    def trusted_proxies(self) -> Set[str]:
        return set(_csv(self.TRUSTED_PROXIES))

    @property
    def ws_ingest_roles(self) -> Set[str]:
        return {role.lower() for role in _csv(self.WS_INGEST_ROLES)}

    @property
    def default_rate_limit(self) -> str:
        return f"{self.RATE_LIMIT_PER_MINUTE}/minute"

    @property
    def reporting_tz(self) -> timezone:
        return timezone(timedelta(minutes=self.REPORTING_UTC_OFFSET_MINUTES))


@lru_cache()
def get_api_settings() -> APISettings:
    return APISettings()


api_settings = get_api_settings()
