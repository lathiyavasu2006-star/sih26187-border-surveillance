import pytest
from pydantic import ValidationError

from backend.core.config import Settings, settings
from backend.core.security import mask_sensitive, mask_url_credentials, validate_password_strength


@pytest.mark.parametrize(
    "score,level",
    [
        (-5, "normal"), (0, "normal"), (20, "normal"),
        (21, "low"), (40, "low"),
        (41, "suspicious"), (60, "suspicious"),
        (61, "high_risk"), (80, "high_risk"),
        (81, "critical"), (100, "critical"), (150, "critical"),
    ],
)
def test_risk_level_boundaries(score, level):
    assert settings.get_risk_level(score) == level


def test_settings_loaded_from_env():
    assert settings.DATABASE_URL.startswith("postgresql+asyncpg://")
    assert settings.MAX_FAILED_LOGIN_ATTEMPTS == 3
    assert settings.ACCOUNT_LOCKOUT_MINUTES == 30
    assert settings.ZONE_RISK_BONUS["no_mans_land"] == 100
    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES == 15


@pytest.mark.parametrize("hour,night", [(22, True), (23, True), (0, True), (4, True), (5, False), (12, False), (21, False)])
def test_night_hours_wrap_midnight(hour, night):
    assert settings.is_night_hour(hour) is night


def test_production_rejects_default_admin_password():
    from backend.core.config import DEFAULT_ADMIN_PASSWORD

    # Explicit password: the guard must not depend on whatever .env currently holds.
    with pytest.raises(ValidationError):
        Settings(ENVIRONMENT="production", ADMIN_PASSWORD=DEFAULT_ADMIN_PASSWORD)
    Settings(ENVIRONMENT="production", ADMIN_PASSWORD="Rotated#Prod2026Pass")  # a changed password is accepted


def test_short_secret_key_rejected():
    with pytest.raises(ValidationError):
        Settings(SECRET_KEY="too-short")


def test_ensure_directories(tmp_path):
    custom = settings.model_copy(
        update={
            name: str(tmp_path / name.lower())
            for name in ["EVIDENCE_PATH", "SNAPSHOTS_PATH", "CLIPS_PATH", "MODELS_PATH", "LOGS_PATH", "DATASETS_PATH"]
        }
    )
    custom.ensure_directories()
    assert (tmp_path / "snapshots_path").is_dir()
    assert (tmp_path / "datasets_path").is_dir()


def test_password_policy():
    assert validate_password_strength("Border#Secure2026")
    for weak in ["short1!A", "alllowercase123!", "ALLUPPERCASE123!", "NoDigitsHere!!!", "NoSpecial12345", "a" * 80 + "A1!"]:
        with pytest.raises(ValueError):
            validate_password_strength(weak)


def test_secret_masking():
    masked = mask_sensitive(
        {"host": "10.1.1.5", "password": "p@ss", "nested": {"api_key": "abc", "list": [{"token": "t"}]}, "empty_password": ""}
    )
    assert masked == {
        "host": "10.1.1.5",
        "password": "***",
        "nested": {"api_key": "***", "list": [{"token": "***"}]},
        "empty_password": "",
    }
    assert mask_url_credentials("rtsp://admin:hunter2@10.0.0.9:554/live") == "rtsp://admin:***@10.0.0.9:554/live"
    assert mask_url_credentials("rtsp://10.0.0.9/live") == "rtsp://10.0.0.9/live"
