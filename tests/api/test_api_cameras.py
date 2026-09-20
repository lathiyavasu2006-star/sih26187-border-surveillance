"""Camera registration, listing order, access scoping, status changes and deletion rules."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.core.enums import AlertType, EvidenceType, RiskLevel
from backend.models import Alert, AuditLog, Camera, Evidence
from backend.models.alert import generate_alert_id

from tests.api.conftest import auth_header


async def make_alert(session, camera_id, risk_score=85, acknowledged=False):
    alert = Alert(
        alert_id=generate_alert_id(),
        camera_id=camera_id,
        alert_type=AlertType.INTRUSION,
        risk_score=risk_score,
        risk_level=RiskLevel.CRITICAL if risk_score > 80 else RiskLevel.SUSPICIOUS,
        risk_reasons=["restricted zone"],
        acknowledged=acknowledged,
        acknowledged_at=datetime.now(timezone.utc) if acknowledged else None,
    )
    session.add(alert)
    await session.commit()
    return alert


async def test_register_generates_sequential_ids_per_region(client, admin_headers, fake_probe, session):
    fake_probe(connected=True)
    first = await client.post("/cameras/register", headers=admin_headers,
                              json={"name": "North 1", "zone_region": "north", "rtsp_url": "rtsp://10.0.0.1/s"})
    second = await client.post("/cameras/register", headers=admin_headers,
                               json={"name": "North 2", "zone_region": "north", "device_id": "0"})
    south = await client.post("/cameras/register", headers=admin_headers,
                              json={"name": "South 1", "zone_region": "south", "rtsp_url": "rtsp://10.0.0.2/s"})

    assert first.json()["camera_id"] == "CAM-N-001"
    assert second.json()["camera_id"] == "CAM-N-002"
    assert south.json()["camera_id"] == "CAM-S-001"

    body = first.json()
    assert body["status"] == "online"
    assert body["connection_ok"] is True
    assert body["preview_frame"] == "/9j/FAKEPREVIEW"
    assert "Stream verified" in body["message"]

    logs = (await session.execute(select(AuditLog).where(AuditLog.action == "REGISTER_CAMERA"))).scalars().all()
    assert len(logs) == 3


async def test_register_with_unreachable_stream_is_degraded(client, admin_headers, fake_probe):
    fake_probe(connected=False, message="Cannot open stream")
    response = await client.post("/cameras/register", headers=admin_headers,
                                 json={"name": "Broken", "zone_region": "east", "rtsp_url": "rtsp://10.9.9.9/x"})
    body = response.json()
    assert response.status_code == 201
    assert body["status"] == "degraded"
    assert body["connection_ok"] is False
    assert body["preview_frame"] is None
    assert "could not be verified" in body["message"]


async def test_register_masks_stream_credentials_in_response(client, admin_headers, fake_probe):
    fake_probe(connected=True)
    response = await client.post("/cameras/register", headers=admin_headers,
                                 json={"name": "Cred", "zone_region": "west",
                                       "rtsp_url": "rtsp://admin:hunter2@10.0.0.5:554/stream"})
    assert response.json()["rtsp_url"] == "rtsp://admin:***@10.0.0.5:554/stream"
    assert "hunter2" not in response.text


async def test_camera_list_sorted_by_active_alerts(client, admin_headers, fake_probe, session):
    fake_probe(connected=True)
    for name in ("A", "B", "C"):
        await client.post("/cameras/register", headers=admin_headers,
                          json={"name": name, "zone_region": "north", "rtsp_url": f"rtsp://10.0.0.1/{name}"})
    await make_alert(session, "CAM-N-002")
    await make_alert(session, "CAM-N-002")
    await make_alert(session, "CAM-N-003")
    await make_alert(session, "CAM-N-001", acknowledged=True)  # acknowledged alerts do not count

    response = await client.get("/cameras", headers=admin_headers)
    items = response.json()["items"]
    assert response.json()["total"] == 3
    assert [item["camera_id"] for item in items] == ["CAM-N-002", "CAM-N-003", "CAM-N-001"]
    assert [item["active_alert_count"] for item in items] == [2, 1, 0]


async def test_camera_list_filters(client, admin_headers, fake_probe):
    fake_probe(connected=True)
    await client.post("/cameras/register", headers=admin_headers,
                      json={"name": "N", "zone_region": "north", "camera_type": "thermal", "rtsp_url": "rtsp://a/1"})
    await client.post("/cameras/register", headers=admin_headers,
                      json={"name": "S", "zone_region": "south", "rtsp_url": "rtsp://a/2"})

    north = await client.get("/cameras", headers=admin_headers, params={"zone_region": "north"})
    assert [c["camera_id"] for c in north.json()["items"]] == ["CAM-N-001"]

    thermal = await client.get("/cameras", headers=admin_headers, params={"camera_type": "thermal"})
    assert thermal.json()["total"] == 1

    offline = await client.get("/cameras", headers=admin_headers, params={"status": "offline"})
    assert offline.json()["total"] == 0


async def test_camera_visibility_scoping(client, camera, operator_headers, outsider, admin_headers):
    """Operator granted CAM-N-001 sees it; an operator scoped to the south region sees nothing."""
    granted = await client.get("/cameras", headers=operator_headers)
    assert [c["camera_id"] for c in granted.json()["items"]] == ["CAM-N-001"]

    other = await client.get("/cameras", headers=auth_header(outsider))
    assert other.json()["items"] == [] and other.json()["total"] == 0

    forbidden = await client.get("/cameras/CAM-N-001", headers=auth_header(outsider))
    assert forbidden.status_code == 403

    assert (await client.get("/cameras/CAM-N-001", headers=operator_headers)).status_code == 200


async def test_camera_detail_includes_recent_alerts(client, camera, admin_headers, session):
    for _ in range(7):
        await make_alert(session, "CAM-N-001")
    response = await client.get("/cameras/CAM-N-001", headers=admin_headers)
    body = response.json()
    assert body["active_alert_count"] == 7
    assert len(body["recent_alerts"]) == 5
    assert body["viewer_count"] == 0


async def test_camera_not_found(client, admin_headers):
    assert (await client.get("/cameras/CAM-N-404", headers=admin_headers)).status_code == 404


async def test_status_change_to_offline_creates_alert(client, camera, supervisor_headers, session):
    response = await client.patch("/cameras/CAM-N-001/status", headers=supervisor_headers, json={"status": "offline"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "offline" and body["previous_status"] == "online"
    assert body["offline_alert_id"]

    alert = (await session.execute(select(Alert).where(Alert.alert_id == body["offline_alert_id"]))).scalar_one()
    assert alert.alert_type == AlertType.CAMERA_OFFLINE
    assert alert.risk_score == 60 and alert.risk_level == RiskLevel.SUSPICIOUS

    back = await client.patch("/cameras/CAM-N-001/status", headers=supervisor_headers, json={"status": "online"})
    assert back.json()["status"] == "online"

    actions = {log.action for log in (await session.execute(select(AuditLog))).scalars().all()}
    assert {"UPDATE_CAMERA_STATUS", "CAMERA_MARKED_OFFLINE"} <= actions


async def test_delete_camera_hard_soft_and_blocked(client, camera, admin_headers, supervisor_headers, session):
    # 1. blocked by evidence
    session.add(Evidence(camera_id="CAM-N-001", evidence_type=EvidenceType.SNAPSHOT,
                         file_path="E:/x.jpg", file_name="x.jpg", file_hash="a" * 64))
    await session.commit()
    blocked = await client.delete("/cameras/CAM-N-001", headers=admin_headers)
    assert blocked.status_code == 409
    assert "existing evidence" in blocked.json()["detail"]

    # supervisors may not delete at all
    assert (await client.delete("/cameras/CAM-N-001", headers=supervisor_headers)).status_code == 403

    # 2. soft delete when only alerts exist
    await session.execute(Evidence.__table__.delete())
    await make_alert(session, "CAM-N-001")
    soft = await client.delete("/cameras/CAM-N-001", headers=admin_headers)
    assert soft.status_code == 200 and soft.json()["deletion"] == "soft"
    camera_row = (await session.execute(select(Camera).where(Camera.camera_id == "CAM-N-001"))).scalar_one()
    await session.refresh(camera_row)
    assert camera_row.status.value == "offline" and camera_row.rtsp_url is None

    # 3. hard delete when there is no history
    await session.execute(Alert.__table__.delete())
    await session.commit()
    hard = await client.delete("/cameras/CAM-N-001", headers=admin_headers)
    assert hard.status_code == 200 and hard.json()["deletion"] == "hard"
    assert (await session.execute(select(Camera))).scalars().all() == []


async def test_camera_test_endpoint(client, camera, supervisor_headers, fake_probe, operator_headers):
    fake_probe(connected=True, fps=24.5)
    response = await client.get("/cameras/CAM-N-001/test", headers=supervisor_headers)
    body = response.json()
    assert response.status_code == 200
    assert body["connected"] is True and body["fps"] == 24.5
    assert body["resolution"] == "1920x1080"
    assert body["frame_preview"] == "/9j/FAKEPREVIEW"

    assert (await client.get("/cameras/CAM-N-001/test", headers=operator_headers)).status_code == 403
