from functools import lru_cache
from pathlib import Path
from typing import Dict, Tuple

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

DEFAULT_ADMIN_PASSWORD = "Admin@SIH2024"
ALLOWED_ENVIRONMENTS = {"development", "staging", "production", "test"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 3600
    DB_ECHO: bool = False

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 10080
    BCRYPT_ROUNDS: int = 12
    MAX_FAILED_LOGIN_ATTEMPTS: int = 3
    ACCOUNT_LOCKOUT_MINUTES: int = 30

    # Storage
    EVIDENCE_PATH: str = "E:/sih26187/evidence/"
    SNAPSHOTS_PATH: str = "E:/sih26187/evidence/snapshots/"
    CLIPS_PATH: str = "E:/sih26187/evidence/clips/"
    MODELS_PATH: str = "E:/sih26187/models/"
    LOGS_PATH: str = "E:/sih26187/logs/"
    DATASETS_PATH: str = "E:/sih26187/datasets/"

    # Surveillance tuning
    CAMERA_OFFLINE_THRESHOLD_SECONDS: int = 30
    HEALTH_CHECK_INTERVAL_SECONDS: int = 30
    LOITER_THRESHOLD_SECONDS: int = 30
    ALERT_COOLDOWN_SECONDS: int = 15
    CONF_THRESHOLD: float = 0.4
    TRAIL_LENGTH: int = 50
    NIGHT_START_HOUR: int = 22
    NIGHT_END_HOUR: int = 5
    NIGHT_BRIGHTNESS_THRESHOLD: int = 80
    NIGHT_RISK_MULTIPLIER: float = 1.5
    HOT_STORAGE_DAYS: int = 7

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    # Bootstrap admin
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = DEFAULT_ADMIN_PASSWORD
    ADMIN_EMAIL: str = "admin@sih26187.gov.in"

    ZONE_RISK_BONUS: Dict[str, int] = {
        "public": 0,
        "buffer": 10,
        "sensitive": 30,
        "restricted": 50,
        "no_mans_land": 100,
    }
    RISK_LEVELS: Dict[str, Tuple[int, int]] = {
        "normal": (0, 20),
        "low": (21, 40),
        "suspicious": (41, 60),
        "high_risk": (61, 80),
        "critical": (81, 100),
    }

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_strength(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return value

    @field_validator("ENVIRONMENT")
    @classmethod
    def _valid_environment(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ALLOWED_ENVIRONMENTS:
            raise ValueError(f"ENVIRONMENT must be one of {sorted(ALLOWED_ENVIRONMENTS)}")
        return value

    @field_validator("CONF_THRESHOLD")
    @classmethod
    def _valid_confidence(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("CONF_THRESHOLD must be between 0 and 1")
        return value

    @field_validator("NIGHT_START_HOUR", "NIGHT_END_HOUR")
    @classmethod
    def _valid_hour(cls, value: int) -> int:
        if not 0 <= value <= 23:
            raise ValueError("Night hours must be between 0 and 23")
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.ENVIRONMENT == "production":
            if self.ADMIN_PASSWORD == DEFAULT_ADMIN_PASSWORD:
                raise ValueError("Default ADMIN_PASSWORD must be changed in production")
            if self.DB_ECHO:
                raise ValueError("DB_ECHO must be disabled in production (leaks query parameters)")
        return self

    def get_risk_level(self, score: int) -> str:
        score = max(0, min(100, int(score)))
        for level, (low, high) in self.RISK_LEVELS.items():
            if low <= score <= high:
                return level
        return "critical"

    def is_night_hour(self, hour: int) -> bool:
        if self.NIGHT_START_HOUR > self.NIGHT_END_HOUR:
            return hour >= self.NIGHT_START_HOUR or hour < self.NIGHT_END_HOUR
        return self.NIGHT_START_HOUR <= hour < self.NIGHT_END_HOUR

    def ensure_directories(self) -> None:
        for path in [
            self.EVIDENCE_PATH,
            self.SNAPSHOTS_PATH,
            self.CLIPS_PATH,
            self.MODELS_PATH,
            self.LOGS_PATH,
            self.DATASETS_PATH,
        ]:
            Path(path).mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
