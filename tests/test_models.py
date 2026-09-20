import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.core.enums import AlertType, CameraStatus, CameraType, EvidenceType, HardwareType, RiskLevel, ZoneType
from backend.core.security import hash_password
from backend.database.database import check_db_connection, engine
from backend.models import (
    Alert,
    AuditLog,
    Camera,
    Event,
    Evidence,
    HardwareRegistry,
    SystemHealth,
    TrackedObject,
    User,
    Zone,
)
from backend.models.alert import generate_alert_id

EXPECTED_TABLES = {
    "cameras", "users", "zones", "alerts", "events", "tracked_objects",
    "evidence", "hardware_registry", "audit_logs", "system_health",
}


async def test_global_engine_connects():
    try:
        assert await check_db_connection() is True
    finally:
        await engine.dispose()


async def test_all_tables_and_indexes_exist(db):
    rows = await db.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
    assert EXPECTED_TABLES <= {r[0] for r in rows}
    idx = (await db.execute(text("SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND indexname LIKE 'idx_%'"))).scalar()
    assert idx == 49  # 48 base indexes + the partial idx_zones_geo for map-drawn zones


async def test_camera_server_defaults(db, camera):
    assert camera.status == CameraStatus.ONLINE
    assert camera.camera_type == CameraType.STANDARD
    assert camera.registered_at.tzinfo is not None
    assert camera.last_seen.tzinfo is not None
    await db.refresh(camera)
    assert camera.gps_lat == Decimal("26.98123456") and camera.gps_lng == Decimal("84.85123456")
    assert "CAM-N01" in repr(camera)


async def test_camera_to_dict_masks_rtsp_credentials(db):
    cam = Camera(camera_id="CAM-S01", name="South", zone_region="south", rtsp_url="rtsp://admin:secret@10.0.0.5/s")
    db.add(cam)
    await db.flush()
    assert cam.to_dict()["rtsp_url"] == "rtsp://admin:***@10.0.0.5/s"
    assert cam.to_dict(include_secrets=True)["rtsp_url"].endswith("secret@10.0.0.5/s")


async def test_camera_offline_detection(camera):
    now = datetime.now(timezone.utc)
    camera.last_seen = now - timedelta(seconds=31)
    assert camera.is_offline(now) is True
    camera.status = CameraStatus.OFFLINE
    camera.mark_seen(now)
    assert camera.is_offline(now) is False
    assert camera.status == CameraStatus.ONLINE


async def test_user_defaults_uuid_and_no_password_in_dict(db, operator):
    assert operator.user_id is not None
    assert operator.is_active is True
    assert operator.failed_login_attempts == 0
    assert operator.created_at.tzinfo is not None
    data = operator.to_dict()
    assert "password_hash" not in data
    assert "password_hash" not in operator.to_dict(exclude=[])
    assert data["is_locked"] is False


async def test_plain_text_password_rejected_by_database(db):
    user = User(username="plainuser", password_hash="Admin@SIH2024", role="operator")
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(user)
            await db.flush()


async def test_username_unique(db, operator):
    dup = User(username="operator1", password_hash=hash_password("Another#Pass2026"), role="operator")
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(dup)
            await db.flush()


def test_lockout_after_three_failures_for_thirty_minutes():
    user = User(username="x", password_hash="$2b$", role="operator", failed_login_attempts=0)
    now = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    assert user.register_failed_login(now) is False
    assert user.register_failed_login(now) is False
    assert user.is_locked(now) is False
    assert user.register_failed_login(now) is True
    assert user.is_locked(now) is True
    assert user.locked_until == now + timedelta(minutes=30)
    assert user.is_locked(now + timedelta(minutes=29, seconds=59)) is True
    assert user.is_locked(now + timedelta(minutes=30)) is False
    # After expiry, a new failure starts a fresh window instead of re-locking immediately.
    later = now + timedelta(minutes=31)
    assert user.register_failed_login(later) is False
    assert user.failed_login_attempts == 1
    user.register_successful_login(later)
    assert user.failed_login_attempts == 0 and user.locked_until is None and user.last_login == later


async def test_zone_jsonb_defaults_and_cascade_delete(db, camera):
    zone = Zone(camera_id=camera.camera_id, zone_name="Fence Line", zone_type=ZoneType.RESTRICTED,
                polygon=[[0, 0], [100, 0], [100, 100], [0, 100]])
    db.add(zone)
    await db.flush()
    assert zone.night_rules == {"multiplier": 1.5, "start": 22, "end": 5}
    assert zone.allowed_persons == []
    assert zone.color_hex == "#00ff00"
    zone_id = zone.zone_id

    await db.execute(delete(Camera).where(Camera.camera_id == camera.camera_id))
    remaining = (await db.execute(text("SELECT count(*) FROM zones WHERE zone_id = :z"), {"z": zone_id})).scalar()
    assert remaining == 0


async def test_zone_raw_insert_uses_server_defaults(db, camera):
    await db.execute(text(
        "INSERT INTO zones (camera_id, zone_name, zone_type, polygon) "
        "VALUES ('CAM-N01', 'raw', 'buffer', '[[0,0],[1,0],[1,1]]')"
    ))
    row = (await db.execute(text("SELECT night_rules, allowed_persons, loiter_threshold_seconds FROM zones WHERE zone_name='raw'"))).one()
    assert row.night_rules == {"multiplier": 1.5, "start": 22, "end": 5}
    assert row.allowed_persons == []
    assert row.loiter_threshold_seconds == 30


def test_zone_geometry_and_night_bonus():
    zone = Zone(zone_name="z", zone_type=ZoneType.SENSITIVE, polygon=[[0, 0], [10, 0], [10, 10], [0, 10]],
                risk_bonus=30, night_rules={"multiplier": 1.5, "start": 22, "end": 5})
    assert zone.contains_point(5, 5) is True
    assert zone.contains_point(15, 5) is False
    assert zone.effective_risk_bonus(datetime(2026, 1, 1, 23, 0)) == 45
    assert zone.effective_risk_bonus(datetime(2026, 1, 1, 12, 0)) == 30


async def test_zone_invalid_color_rejected(db, camera):
    zone = Zone(camera_id=camera.camera_id, zone_name="bad", zone_type=ZoneType.PUBLIC,
                polygon=[[0, 0], [1, 0], [1, 1]], color_hex="green!!")
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(zone)
            await db.flush()


async def test_invalid_enum_value_rejected(db):
    with pytest.raises(DBAPIError):
        async with db.begin_nested():
            await db.execute(text(
                "INSERT INTO cameras (camera_id, name, zone_region, camera_type) VALUES ('CAM-X1', 'x', 'north', 'laser')"
            ))


async def test_alert_defaults_restrict_and_set_null(db, camera, operator):
    alert = Alert(camera_id=camera.camera_id, track_id=7, alert_type=AlertType.INTRUSION,
                  risk_score=85, risk_level=RiskLevel.CRITICAL, risk_reasons=["restricted zone", "night"])
    db.add(alert)
    await db.flush()
    assert alert.alert_id.startswith("ALT-") and len(alert.alert_id) <= 40
    assert alert.acknowledged is False and alert.false_alarm is False
    assert alert.timestamp.tzinfo is not None

    alert.acknowledge(operator.user_id, notes="Patrol dispatched")
    await db.flush()
    assert alert.acknowledged_at is not None

    # Deleting a camera that has alerts is blocked (evidence preservation).
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(delete(Camera).where(Camera.camera_id == camera.camera_id))

    # Deleting the acknowledging user keeps the alert and nulls the reference.
    await db.execute(delete(User).where(User.user_id == operator.user_id))
    ack_by = (await db.execute(text("SELECT acknowledged_by FROM alerts WHERE alert_id=:a"), {"a": alert.alert_id})).scalar()
    assert ack_by is None


async def test_alert_risk_score_check(db, camera):
    alert = Alert(alert_id=generate_alert_id(), camera_id=camera.camera_id, alert_type=AlertType.WEAPON,
                  risk_score=150, risk_level=RiskLevel.CRITICAL)
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(alert)
            await db.flush()


async def test_alert_ack_requires_timestamp(db, camera):
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(Alert(camera_id=camera.camera_id, alert_type=AlertType.SMOKE, risk_score=10,
                         risk_level=RiskLevel.NORMAL, acknowledged=True))
            await db.flush()


async def test_evidence_sha256_integrity(db, camera, tmp_path):
    clip = tmp_path / "snap_001.jpg"
    clip.write_bytes(b"\xff\xd8 frame bytes " * 1000)
    evidence = Evidence.from_file(clip, camera.camera_id, EvidenceType.SNAPSHOT, track_id=3)
    db.add(evidence)
    await db.flush()

    assert evidence.file_hash == hashlib.sha256(clip.read_bytes()).hexdigest()
    assert len(evidence.file_hash) == 64
    assert evidence.file_size_bytes == clip.stat().st_size
    assert evidence.is_hot_storage is True
    assert evidence.verify_integrity() is True

    clip.write_bytes(b"tampered")
    assert evidence.verify_integrity() is False
    clip.unlink()
    assert evidence.verify_integrity() is False

    evidence.archive()
    await db.flush()
    assert evidence.is_hot_storage is False and evidence.archived_at is not None


async def test_evidence_rejects_non_sha256_hash(db, camera):
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(Evidence(camera_id=camera.camera_id, evidence_type=EvidenceType.VIDEO_CLIP,
                            file_path="E:/x.mp4", file_hash="md5-not-allowed"))
            await db.flush()


async def test_evidence_blocks_alert_deletion(db, camera):
    alert = Alert(camera_id=camera.camera_id, alert_type=AlertType.LOITERING, risk_score=50, risk_level=RiskLevel.SUSPICIOUS)
    db.add(alert)
    await db.flush()
    db.add(Evidence(alert_id=alert.alert_id, camera_id=camera.camera_id, evidence_type=EvidenceType.SNAPSHOT,
                    file_path="E:/s.jpg", file_hash="a" * 64))
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(delete(Alert).where(Alert.alert_id == alert.alert_id))


def test_evidence_from_missing_file():
    with pytest.raises(FileNotFoundError):
        Evidence.from_file("E:/does/not/exist.jpg", "CAM-N01", EvidenceType.SNAPSHOT)


async def test_event_positions_and_order_constraint(db, camera):
    start = datetime(2026, 9, 15, 23, 0, tzinfo=timezone.utc)
    event = Event(track_id=11, camera_id=camera.camera_id, first_seen=start, last_seen=start)
    for i in range(60):
        event.record_position(i, i * 2, start + timedelta(seconds=i), zone_name="Fence" if i > 30 else "Buffer", risk_score=i)
    db.add(event)
    await db.flush()
    assert len(event.positions) == 50
    assert event.total_time_seconds == 59
    assert event.max_risk_score == 59
    assert [z["zone"] for z in event.zone_history] == ["Buffer", "Fence"]
    assert event.object_class == "person" and event.alert_count == 0

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(Event(track_id=12, camera_id=camera.camera_id, first_seen=start, last_seen=start - timedelta(seconds=1)))
            await db.flush()


async def test_tracked_object_defaults_and_bbox(db, camera):
    obj = TrackedObject(track_id=5, camera_id=camera.camera_id, object_class="person", confidence=Decimal("0.912"))
    obj.set_bbox(200, 300, 100, 150)
    db.add(obj)
    await db.flush()
    assert (obj.bbox_x1, obj.bbox_y1, obj.bbox_x2, obj.bbox_y2) == (100, 150, 200, 300)
    assert (obj.cx, obj.cy) == (150, 225)
    assert obj.direction == "stationary" and obj.risk_level == "normal" and obj.in_fence is False

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(TrackedObject(track_id=6, camera_id=camera.camera_id, object_class="car", confidence=Decimal("1.5")))
            await db.flush()


async def test_hardware_defaults_masking_and_set_null(db, camera):
    hw = HardwareRegistry(hardware_type=HardwareType.PTZ_CAMERA, name="PTZ-1", camera_id=camera.camera_id,
                          connection_config={"ip": "10.0.0.7", "username": "admin", "password": "hikvision123"})
    bare = HardwareRegistry(hardware_type=HardwareType.RADAR, name="Radar-1")
    db.add_all([hw, bare])
    await db.flush()
    assert bare.connection_config == {} and bare.capabilities == [] and bare.status.value == "standby"
    assert hw.to_dict()["connection_config"]["password"] == "***"
    assert hw.to_dict(include_secrets=True)["connection_config"]["password"] == "hikvision123"

    await db.execute(delete(Camera).where(Camera.camera_id == camera.camera_id))
    cam_ref = (await db.execute(text("SELECT camera_id FROM hardware_registry WHERE hardware_id=:h"), {"h": hw.hardware_id})).scalar()
    assert cam_ref is None


async def test_audit_log_masks_and_defaults(db, operator):
    entry = AuditLog.record("user_update", user_id=operator.user_id, username=operator.username, table_name="users",
                            record_id=operator.user_id, new_value={"password": "NewPass#2026", "role": "supervisor"},
                            ip_address="10.2.3.4")
    db.add(entry)
    await db.flush()
    assert entry.status == "success" and entry.timestamp.tzinfo is not None
    stored = (await db.execute(select(AuditLog.new_value).where(AuditLog.log_id == entry.log_id))).scalar()
    assert stored == {"password": "***", "role": "supervisor"}


async def test_system_health_capture(db):
    snapshot = SystemHealth.capture_host_metrics(disk_path="E:/", region="north")
    snapshot.cameras_total, snapshot.cameras_online = 4, 3
    db.add(snapshot)
    await db.flush()
    assert snapshot.id is not None and snapshot.server_name == "local"
    assert 0 <= float(snapshot.cpu_percent) <= 100
    assert float(snapshot.ram_total_gb) > 0

    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(SystemHealth(cameras_total=1, cameras_online=5))
            await db.flush()
