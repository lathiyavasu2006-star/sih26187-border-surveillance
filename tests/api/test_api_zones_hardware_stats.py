"""Zones, hardware registry (secret masking) and the stats/health endpoints."""
import pytest
from sqlalchemy import select

from backend.models import AuditLog, HardwareRegistry, SystemHealth, Zone

from tests.api.conftest import auth_header

ZONE_PAYLOAD = {
    "zone_name": "Fence Line North",
    "zone_type": "restricted",
    "polygon": [[100, 100], [500, 100], [500, 400], [100, 400]],
}

HARDWARE_PAYLOAD = {
    "hardware_type": "thermal_camera",
    "name": "Thermal Unit North 1",
    "manufacturer": "FLIR",
    "connection_config": {"rtsp_url": "rtsp://10.0.0.31:554/thermal", "username": "operator", "password": "hikvision123"},
    "capabilities": ["thermal", "night_vision"],
}


# --------------------------------------------------------------------------- zones

async def test_create_zone_sets_risk_bonus_from_type(client, camera, supervisor_headers, session):
    response = await client.post("/zones", headers=supervisor_headers, json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["risk_bonus"] == 50            # restricted
    assert body["night_rules"] == {"multiplier": 1.5, "start": 22, "end": 5}
    assert body["polygon"] == ZONE_PAYLOAD["polygon"]

    # a client-supplied risk_bonus cannot weaken the policy value
    tampered = await client.post("/zones", headers=supervisor_headers,
                                 json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001", "zone_name": "NML",
                                       "zone_type": "no_mans_land", "risk_bonus": 0})
    assert tampered.json()["risk_bonus"] == 100

    log = (await session.execute(select(AuditLog).where(AuditLog.action == "CREATE_ZONE"))).scalars().first()
    assert log.new_value["zone_type"] == "restricted"


async def test_zone_polygon_validation(client, camera, supervisor_headers):
    too_few = await client.post("/zones", headers=supervisor_headers,
                                json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001", "polygon": [[0, 0], [10, 10]]})
    assert too_few.status_code == 422

    fractional = await client.post("/zones", headers=supervisor_headers,
                                   json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001",
                                         "polygon": [[0.5, 0], [10, 0], [10, 10]]})
    assert fractional.status_code == 422
    assert "integer pixel" in str(fractional.json()["detail"])

    negative = await client.post("/zones", headers=supervisor_headers,
                                 json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001",
                                       "polygon": [[-5, 0], [10, 0], [10, 10]]})
    assert negative.status_code == 422


async def test_zone_update_delete_and_listing(client, camera, supervisor_headers, admin_headers, operator_headers, session):
    zone_id = (await client.post("/zones", headers=supervisor_headers,
                                 json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001"})).json()["zone_id"]

    updated = await client.put(f"/zones/{zone_id}", headers=supervisor_headers,
                               json={"zone_name": "Fence Line North (revised)", "zone_type": "buffer",
                                     "loiter_threshold_seconds": 20})
    assert updated.status_code == 200
    assert updated.json()["zone_name"] == "Fence Line North (revised)"
    assert updated.json()["risk_bonus"] == 10          # recomputed for the new type
    assert updated.json()["loiter_threshold_seconds"] == 20

    by_camera = await client.get("/zones/CAM-N-001", headers=operator_headers)
    assert by_camera.json()["total"] == 1

    assert (await client.delete(f"/zones/{zone_id}", headers=operator_headers)).status_code == 403
    deleted = await client.delete(f"/zones/{zone_id}", headers=supervisor_headers)
    assert deleted.status_code == 200 and deleted.json()["message"] == "Zone deleted"

    zone = (await session.execute(select(Zone))).scalar_one()
    await session.refresh(zone)
    assert zone.is_active is False                     # soft delete keeps the record

    active_only = await client.get("/zones/CAM-N-001", headers=admin_headers)
    assert active_only.json()["total"] == 0
    with_inactive = await client.get("/zones/CAM-N-001", headers=admin_headers, params={"include_inactive": True})
    assert with_inactive.json()["total"] == 1


async def test_zone_requires_camera_access(client, camera, outsider, session):
    response = await client.post("/zones", headers=auth_header(outsider),
                                 json={**ZONE_PAYLOAD, "camera_id": "CAM-N-001"})
    assert response.status_code == 403


# --------------------------------------------------------------------------- hardware

async def test_hardware_registration_masks_secrets(client, camera, admin_headers, session):
    response = await client.post("/hardware/register", headers=admin_headers,
                                 json={**HARDWARE_PAYLOAD, "camera_id": "CAM-N-001"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["connection_config"]["password"] == "***"
    assert body["connection_config"]["rtsp_url"] == "rtsp://10.0.0.31:554/thermal"
    assert "hikvision123" not in response.text

    stored = (await session.execute(select(HardwareRegistry))).scalar_one()
    assert stored.connection_config["password"] == "hikvision123", "the real secret must still be stored"


async def test_hardware_status_board_groups_by_type(client, camera, admin_headers, operator_headers):
    await client.post("/hardware/register", headers=admin_headers, json=HARDWARE_PAYLOAD)
    await client.post("/hardware/register", headers=admin_headers,
                      json={"hardware_type": "radar", "name": "Radar 1", "status": "connected"})

    response = await client.get("/hardware/status", headers=operator_headers)
    body = response.json()
    assert set(body) == {"standard_camera", "ptz_camera", "thermal_camera", "drone", "radar",
                         "license_plate_scanner", "satellite"}
    assert body["thermal_camera"]["total"] == 1 and body["thermal_camera"]["standby"] == 1
    assert body["radar"]["total"] == 1 and body["radar"]["connected"] == 1
    assert body["drone"]["total"] == 0 and body["drone"]["items"] == []
    assert body["thermal_camera"]["items"][0]["connection_config"]["password"] == "***"
    assert "hikvision123" not in response.text


async def test_hardware_test_connection(client, camera, admin_headers, supervisor_headers, fake_probe, session):
    hardware_id = (await client.post("/hardware/register", headers=admin_headers, json=HARDWARE_PAYLOAD)).json()["hardware_id"]

    fake_probe(connected=True)
    ok = await client.post(f"/hardware/{hardware_id}/test-connection", headers=supervisor_headers)
    assert ok.status_code == 200 and ok.json()["connected"] is True and ok.json()["status"] == "connected"

    fake_probe(connected=False, message="Cannot open stream")
    failed = await client.post(f"/hardware/{hardware_id}/test-connection", headers=supervisor_headers)
    assert failed.json()["connected"] is False and failed.json()["status"] == "error"

    hardware = (await session.execute(select(HardwareRegistry))).scalar_one()
    await session.refresh(hardware)
    assert hardware.status.value == "error" and hardware.last_seen is not None


async def test_hardware_patch_keeps_masked_secret(client, admin_headers, session):
    hardware_id = (await client.post("/hardware/register", headers=admin_headers, json=HARDWARE_PAYLOAD)).json()["hardware_id"]

    response = await client.patch(f"/hardware/{hardware_id}", headers=admin_headers,
                                  json={"notes": "September maintenance",
                                        "connection_config": {"rtsp_url": "rtsp://10.0.0.32:554/thermal",
                                                              "username": "operator", "password": "***"}})
    assert response.status_code == 200
    stored = (await session.execute(select(HardwareRegistry))).scalar_one()
    await session.refresh(stored)
    assert stored.connection_config["password"] == "hikvision123"   # masked value did not overwrite the secret
    assert stored.connection_config["rtsp_url"] == "rtsp://10.0.0.32:554/thermal"
    assert stored.notes == "September maintenance"


async def test_hardware_delete_admin_only(client, admin_headers, supervisor_headers, session):
    hardware_id = (await client.post("/hardware/register", headers=admin_headers, json=HARDWARE_PAYLOAD)).json()["hardware_id"]
    assert (await client.delete(f"/hardware/{hardware_id}", headers=supervisor_headers)).status_code == 403
    assert (await client.delete(f"/hardware/{hardware_id}", headers=admin_headers)).status_code == 204
    assert (await session.execute(select(HardwareRegistry))).scalars().all() == []


# --------------------------------------------------------------------------- stats

async def test_live_stats(client, camera, admin_headers):
    response = await client.get("/stats", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["cameras_total"] == 1 and body["cameras_online"] == 1
    assert body["active_alerts"] == 0
    assert 0 <= body["cpu_percent"] <= 100
    assert 0 <= body["ram_percent"] <= 100
    assert body["disk_free_gb"] > 0
    assert body["uptime_seconds"] >= 0
    assert body["websocket_clients"] == 0
    # This machine has an RTX 3050, so nvidia-smi must answer.
    assert body["gpu_memory_total_mb"] and body["gpu_memory_total_mb"] > 0
    assert body["gpu_percent"] is not None


async def test_stats_scoped_for_non_admin(client, camera, outsider):
    response = await client.get("/stats", headers=auth_header(outsider))
    assert response.json()["cameras_total"] == 0


async def test_audit_log_browser(client, camera, admin_headers, supervisor_headers):
    await client.get("/cameras", headers=supervisor_headers)

    response = await client.get("/stats/audit-log", headers=admin_headers)
    assert response.status_code == 200
    actions = {item["action"] for item in response.json()["items"]}
    assert {"REGISTER_CAMERA", "LIST_CAMERAS"} <= actions
    assert all("password_hash" not in str(item) for item in response.json()["items"])

    filtered = await client.get("/stats/audit-log", headers=admin_headers, params={"action": "REGISTER_CAMERA"})
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["action"] == "REGISTER_CAMERA"


async def test_system_health_history(client, admin_headers, session):
    from backend.services.system_metrics import record_system_health

    await record_system_health()
    response = await client.get("/stats/system-health-history", headers=admin_headers, params={"hours": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    sample = response.json()["items"][0]
    assert sample["server_name"] == "local"
    assert sample["ram_total_gb"] > 0
    assert (await session.execute(select(SystemHealth))).scalars().all()


async def test_health_endpoint_reports_monitor_state(client):
    response = await client.get("/health")
    body = response.json()
    assert body["db"] == "connected"
    # Lifespan does not run under ASGITransport, so the monitor task is absent here.
    assert body["camera_monitor"] in {"running", "stopped", "disabled"}
