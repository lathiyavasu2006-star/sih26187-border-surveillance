from datetime import datetime, timedelta, timezone

import pytest

from backend.core.enums import AlertType, EvidenceType, RiskLevel, ZoneType
from backend.core.security import verify_password
from backend.database.crud import (
    AuthStatus,
    alert_crud,
    audit_log_crud,
    camera_crud,
    evidence_crud,
    user_crud,
    zone_crud,
)
from backend.database.crud_base import CRUDBase
from backend.models import Alert, Camera, Evidence
from backend.schemas import AlertAcknowledge, AlertCreate, AlertFilter, CameraCreate, UserCreate, ZoneCreate

from tests.conftest import STRONG_PASSWORD


def test_crud_detects_non_id_primary_keys():
    assert CRUDBase(Camera).pk_name == "camera_id"
    assert CRUDBase(Alert).pk_name == "alert_id"


async def test_generic_crud_lifecycle_string_pk(db):
    created = await camera_crud.create(db, CameraCreate(camera_id="cam-e01", name="East 1", zone_region="east"))
    assert created.camera_id == "CAM-E01"
    await camera_crud.create(db, {"camera_id": "CAM-E02", "name": "East 2", "zone_region": "east", "status": "offline"})

    assert (await camera_crud.get(db, "CAM-E01")).name == "East 1"
    assert await camera_crud.exists(db, "CAM-E02") is True
    assert await camera_crud.exists(db, "CAM-NOPE") is False
    assert await camera_crud.count(db, {"zone_region": "east"}) == 2
    assert await camera_crud.count(db, {"status": "offline"}) == 1
    assert [c.camera_id for c in await camera_crud.get_multi(db, filters={"zone_region": "east"})] == ["CAM-E01", "CAM-E02"]
    assert len(await camera_crud.get_multi(db, skip=1, limit=1, filters={"zone_region": "east"})) == 1
    assert await camera_crud.count(db, {"camera_id": ["CAM-E01", "CAM-E02"]}) == 2

    updated = await camera_crud.update(db, created, {"name": "East Tower 1", "location_name": None, "camera_id": "HACK"})
    assert updated.name == "East Tower 1" and updated.camera_id == "CAM-E01"

    deleted = await camera_crud.delete(db, "CAM-E02")
    assert deleted is not None and await camera_crud.get(db, "CAM-E02") is None
    assert await camera_crud.delete(db, "CAM-E02") is None


async def test_generic_crud_integer_pk_and_unknown_filter(db, camera):
    zone = await zone_crud.create(db, ZoneCreate(camera_id="cam-n01", zone_name="No Man's Land",
                                                 zone_type=ZoneType.NO_MANS_LAND, polygon=[[0, 0], [5, 0], [5, 5]]))
    assert zone.risk_bonus == 100 and zone.camera_id == "CAM-N01"
    assert (await zone_crud.get(db, zone.zone_id)).zone_name == "No Man's Land"
    with pytest.raises(ValueError):
        await zone_crud.count(db, {"not_a_column": 1})


async def test_user_create_hashes_password_and_blocks_password_update(db):
    user = await user_crud.create(db, UserCreate(username="Supervisor.North", password=STRONG_PASSWORD,
                                                 role="supervisor", zone_access=["north", "north"]))
    assert user.username == "supervisor.north"
    assert user.password_hash != STRONG_PASSWORD and user.password_hash.startswith("$2b$12$")
    assert verify_password(STRONG_PASSWORD, user.password_hash)
    assert user.zone_access == ["north"]

    old_hash = user.password_hash
    await user_crud.update(db, user, {"password_hash": "plain", "password": "x", "is_active": False})
    assert user.password_hash == old_hash and user.is_active is False


async def test_authentication_lockout_policy(db, operator):
    result = await user_crud.authenticate(db, "operator1", STRONG_PASSWORD)
    assert result.status == AuthStatus.SUCCESS and result.user.last_login is not None

    assert (await user_crud.authenticate(db, "operator1", "wrong-1")).status == AuthStatus.INVALID_CREDENTIALS
    assert (await user_crud.authenticate(db, "operator1", "wrong-2")).status == AuthStatus.INVALID_CREDENTIALS
    third = await user_crud.authenticate(db, "operator1", "wrong-3")
    assert third.status == AuthStatus.LOCKED
    assert 29 * 60 < third.locked_seconds_remaining <= 30 * 60

    # Correct password is refused while locked.
    locked = await user_crud.authenticate(db, "operator1", STRONG_PASSWORD)
    assert locked.status == AuthStatus.LOCKED and locked.user is None

    # Simulate the 30 minutes elapsing.
    operator.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db.flush()
    unlocked = await user_crud.authenticate(db, "operator1", STRONG_PASSWORD)
    assert unlocked.ok
    assert operator.failed_login_attempts == 0 and operator.locked_until is None


async def test_authentication_unknown_and_inactive(db, operator):
    assert (await user_crud.authenticate(db, "ghost", STRONG_PASSWORD)).status == AuthStatus.INVALID_CREDENTIALS
    operator.is_active = False
    await db.flush()
    assert (await user_crud.authenticate(db, "operator1", STRONG_PASSWORD)).status == AuthStatus.INACTIVE


async def test_set_password_unlocks(db, operator):
    for _ in range(3):
        await user_crud.authenticate(db, "operator1", "bad")
    assert operator.is_locked()
    await user_crud.set_password(db, operator, "Fresh#Password2026")
    assert not operator.is_locked()
    assert (await user_crud.authenticate(db, "operator1", "Fresh#Password2026")).ok


async def test_alert_search_and_acknowledge(db, camera, operator):
    base = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
    specs = [(AlertType.INTRUSION, 90), (AlertType.LOITERING, 45), (AlertType.INTRUSION, 15), (AlertType.WEAPON, 99)]
    created = []
    for i, (alert_type, score) in enumerate(specs):
        payload = AlertCreate(camera_id="CAM-N01", alert_type=alert_type, risk_score=score, timestamp=base + timedelta(minutes=i))
        created.append(await alert_crud.create(db, payload))
    assert created[0].risk_level == RiskLevel.CRITICAL and created[2].risk_level == RiskLevel.NORMAL

    items, total, unack = await alert_crud.search(db, AlertFilter(camera_id="cam-n01"))
    assert total == 4 and unack == 4
    assert items[0].timestamp >= items[-1].timestamp

    items, total, _ = await alert_crud.search(db, AlertFilter(alert_type="intrusion", risk_level="critical"))
    assert total == 1 and items[0].alert_id == created[0].alert_id

    _, total, _ = await alert_crud.search(db, AlertFilter(date_from=base + timedelta(minutes=1), date_to=base + timedelta(minutes=2)))
    assert total == 2

    acked = await alert_crud.acknowledge(db, created[2].alert_id, AlertAcknowledge(
        acknowledged_by=operator.user_id, false_alarm=True, notes="Stray cattle"))
    assert acked.acknowledged and acked.false_alarm and acked.acknowledged_at is not None
    with pytest.raises(ValueError):
        await alert_crud.acknowledge(db, created[2].alert_id, AlertAcknowledge(acknowledged_by=operator.user_id))
    assert await alert_crud.acknowledge(db, "ALT-DOES-NOT-EXIST", AlertAcknowledge(acknowledged_by=operator.user_id)) is None

    _, total, unack = await alert_crud.search(db, AlertFilter())
    assert total == 4 and unack == 3
    assert await alert_crud.unacknowledged_count(db, "CAM-N01") == 3


async def test_evidence_archive_and_verify(db, camera, tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"video" * 100)
    ev = Evidence.from_file(f, camera.camera_id, EvidenceType.VIDEO_CLIP, duration_seconds=10)
    db.add(ev)
    await db.flush()
    assert await evidence_crud.verify(db, ev.evidence_id) is True
    assert await evidence_crud.verify(db, 999999) is None
    archived = await evidence_crud.archive_older_than(db, datetime.now(timezone.utc) + timedelta(days=1))
    assert archived >= 1 and ev.is_hot_storage is False


async def test_audit_log_crud(db, operator):
    await audit_log_crud.log(db, "login_success", user=operator, ip_address="10.0.0.1")
    await audit_log_crud.log(db, "login_failed", username="operator1", user_id=operator.user_id, status="failure")
    logs = await audit_log_crud.for_user(db, operator.user_id)
    assert {log.action for log in logs} == {"login_success", "login_failed"}
