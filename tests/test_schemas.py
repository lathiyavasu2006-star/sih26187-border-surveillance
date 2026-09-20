from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.core.enums import RiskLevel, ZoneType
from backend.core.security import hash_password
from backend.models import Camera, HardwareRegistry, User
from backend.schemas import (
    AlertAcknowledge,
    AlertCreate,
    AlertFilter,
    AlertListResponse,
    AlertUpdate,
    AuditLogCreate,
    CameraCreate,
    CameraResponse,
    EventCreate,
    EvidenceCreate,
    EvidenceUpdate,
    HardwareResponse,
    SystemHealthCreate,
    TrackedObjectCreate,
    UserCreate,
    UserListResponse,
    UserPasswordChange,
    UserResponse,
    ZoneCreate,
    ZoneUpdate,
)

NOW = datetime.now(timezone.utc)


def test_user_create_password_policy_and_repr():
    user = UserCreate(username="Op_North", password="Border#Secure2026", role="operator")
    assert user.username == "op_north"
    assert "Border#Secure2026" not in repr(user)
    for weak in ["password", "Password1234", "border#secure2026"]:
        with pytest.raises(ValidationError):
            UserCreate(username="op", password=weak, role="operator")
    with pytest.raises(ValidationError):
        UserCreate(username="admin", password="Admin#Admin2026x", role="admin")
    with pytest.raises(ValidationError):
        UserCreate(username="op_x", password="Border#Secure2026", role="superuser")


def test_user_password_change_must_differ():
    with pytest.raises(ValidationError):
        UserPasswordChange(current_password="Border#Secure2026", new_password="Border#Secure2026")


async def test_user_response_never_exposes_password_hash(db, operator):
    response = UserResponse.model_validate(operator)
    dumped = response.model_dump()
    assert "password_hash" not in dumped and "password" not in dumped
    assert "password_hash" not in response.model_dump_json()
    assert "password_hash" not in UserResponse.model_fields
    listing = UserListResponse(items=[response], total=1)
    assert "$2b$" not in listing.model_dump_json()


def test_extra_fields_forbidden_on_input():
    with pytest.raises(ValidationError):
        UserCreate(username="op_y", password="Border#Secure2026", role="operator", password_hash="$2b$...")
    with pytest.raises(ValidationError):
        CameraCreate(camera_id="CAM-1", name="c", zone_region="north", is_admin=True)


def test_camera_schema_validation_and_masking():
    cam = CameraCreate(camera_id="cam-w01", name="West", zone_region="west", gps_lat=26.5, gps_lng=88.1,
                       rtsp_url="rtsp://u:p@10.0.0.1/stream")
    assert cam.camera_id == "CAM-W01"
    with pytest.raises(ValidationError):
        CameraCreate(camera_id="C", name="x", zone_region="north")
    with pytest.raises(ValidationError):
        CameraCreate(camera_id="CAM-1", name="x", zone_region="north", gps_lat=91)
    with pytest.raises(ValidationError):
        CameraCreate(camera_id="CAM-1", name="x", zone_region="north", rtsp_url="ftp://x")

    orm = Camera(camera_id="CAM-W01", name="West", zone_region="west", camera_type="thermal", status="online",
                 gps_lat="26.50000000", registered_at=NOW, last_seen=NOW, rtsp_url="rtsp://u:p@10.0.0.1/stream")
    resp = CameraResponse.model_validate(orm)
    assert resp.rtsp_url == "rtsp://u:***@10.0.0.1/stream"
    assert resp.gps_lat == 26.5


def test_hardware_response_masks_passwords():
    hw = HardwareRegistry(hardware_id=1, hardware_type="drone", name="Drone-1", status="standby", registered_at=NOW,
                          connection_config={"host": "10.9.9.9", "password": "dji-secret",
                                             "mavlink": {"auth_token": "tok"}, "stream": "rtsp://pilot:pw@10.9.9.9/v"},
                          capabilities=["thermal", "night_vision"])
    resp = HardwareResponse.model_validate(hw)
    assert resp.connection_config["password"] == "***"
    assert resp.connection_config["mavlink"]["auth_token"] == "***"
    assert resp.connection_config["stream"] == "rtsp://pilot:***@10.9.9.9/v"
    assert resp.connection_config["host"] == "10.9.9.9"
    assert "dji-secret" not in resp.model_dump_json()


def test_alert_risk_level_derived_and_validated():
    alert = AlertCreate(camera_id="CAM-N01", alert_type="intrusion", risk_score=62, risk_reasons=["night"])
    assert alert.risk_level == RiskLevel.HIGH_RISK and alert.alert_id.startswith("ALT-")
    with pytest.raises(ValidationError):
        AlertCreate(camera_id="CAM-N01", alert_type="intrusion", risk_score=10, risk_level="critical")
    with pytest.raises(ValidationError):
        AlertCreate(camera_id="CAM-N01", alert_type="intrusion", risk_score=101)
    with pytest.raises(ValidationError):
        AlertCreate(camera_id="CAM-N01", alert_type="drone_strike", risk_score=50)
    assert AlertUpdate(risk_score=85).risk_level == RiskLevel.CRITICAL


def test_alert_filter_and_acknowledge():
    AlertFilter()
    AlertFilter(camera_id="CAM-N01", alert_type="weapon", risk_level="critical", acknowledged=False,
                date_from=datetime(2026, 1, 1, tzinfo=timezone.utc), date_to=datetime(2026, 2, 1, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        AlertFilter(date_from=datetime(2026, 2, 1, tzinfo=timezone.utc), date_to=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValidationError):
        AlertFilter(date_from=datetime(2026, 2, 1))  # naive datetimes are rejected
    with pytest.raises(ValidationError):
        AlertAcknowledge(acknowledged_by=uuid4(), false_alarm=True)
    ack = AlertAcknowledge(acknowledged_by=str(uuid4()), false_alarm=True, notes="Wildlife")
    assert ack.false_alarm is True
    assert AlertListResponse(items=[], total=0, unacknowledged_count=0).unacknowledged_count == 0


def test_zone_schema_rules():
    zone = ZoneCreate(camera_id="CAM-N01", zone_name="Buffer", zone_type=ZoneType.BUFFER,
                      polygon=[[0, 0], [10, 0], [10, 10]], night_rules={"multiplier": 2})
    assert zone.risk_bonus == 10
    assert zone.night_rules == {"multiplier": 2.0, "start": 22, "end": 5}
    assert ZoneCreate(camera_id="CAM-N01", zone_name="R", zone_type="restricted", polygon=[[0, 0], [1, 0], [1, 1]], risk_bonus=5).risk_bonus == 5
    with pytest.raises(ValidationError):
        ZoneCreate(camera_id="CAM-N01", zone_name="x", zone_type="public", polygon=[[0, 0], [1, 1]])
    with pytest.raises(ValidationError):
        ZoneCreate(camera_id="CAM-N01", zone_name="x", zone_type="public", polygon=[[0, 0], [0, 0], [0, 0]])
    with pytest.raises(ValidationError):
        ZoneCreate(camera_id="CAM-N01", zone_name="x", zone_type="public", polygon=[[0, 0], [1, 0], [1, 1]], color_hex="red")
    with pytest.raises(ValidationError):
        ZoneUpdate(night_rules={"start": 30})
    assert ZoneUpdate().model_dump(exclude_unset=True) == {}


def test_evidence_schema_hash_rules():
    ev = EvidenceCreate(camera_id="CAM-N01", evidence_type="snapshot", file_path="E:/sih26187/evidence/snapshots/a.jpg",
                        file_hash="A" * 64)
    assert ev.file_hash == "a" * 64 and ev.file_name == "a.jpg"
    with pytest.raises(ValidationError):
        EvidenceCreate(camera_id="CAM-N01", evidence_type="snapshot", file_path="a.jpg", file_hash="abc")
    with pytest.raises(ValidationError):
        EvidenceCreate(camera_id="CAM-N01", evidence_type="snapshot", file_path="a.jpg", file_hash="a" * 64, duration_seconds=5)
    with pytest.raises(ValidationError):
        EvidenceUpdate(file_hash="b" * 64)  # hash is immutable
    with pytest.raises(ValidationError):
        EvidenceUpdate(is_hot_storage=False)


def test_event_tracked_health_and_audit_schemas():
    event = EventCreate(track_id=1, camera_id="CAM-N01", first_seen=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                        last_seen=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc))
    assert event.total_time_seconds == 120 and event.positions == []
    with pytest.raises(ValidationError):
        EventCreate(track_id=1, camera_id="CAM-N01", first_seen=NOW, last_seen=datetime(2000, 1, 1, tzinfo=timezone.utc))

    obj = TrackedObjectCreate(track_id=1, camera_id="CAM-N01", object_class="person", bbox_x1=10, bbox_y1=20, bbox_x2=30, bbox_y2=60)
    assert (obj.cx, obj.cy) == (20, 40)
    with pytest.raises(ValidationError):
        TrackedObjectCreate(track_id=1, camera_id="CAM-N01", object_class="person", bbox_x1=10)
    with pytest.raises(ValidationError):
        TrackedObjectCreate(track_id=1, camera_id="CAM-N01", object_class="person", direction="up")

    with pytest.raises(ValidationError):
        SystemHealthCreate(cameras_total=2, cameras_online=3)
    with pytest.raises(ValidationError):
        SystemHealthCreate(cpu_percent=120)

    audit = AuditLogCreate(action="hardware_update", ip_address="192.168.1.10", new_value={"password": "x"})
    assert audit.new_value == {"password": "***"}
    with pytest.raises(ValidationError):
        AuditLogCreate(action="x", ip_address="999.1.1.1")
