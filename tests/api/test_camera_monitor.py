"""Background camera monitor: offline detection within 30 s, recovery, neighbours, heartbeats."""
import time

import pytest
from sqlalchemy import select

from backend.core.api_config import api_settings
from backend.core.config import settings
from backend.core.enums import AlertType, CameraStatus, RiskLevel
from backend.core.runtime import runtime_state
from backend.models import Alert, AuditLog, Camera
from backend.services import camera_monitor
from backend.services.camera_monitor import (
    cycle_period_seconds,
    heartbeat_fresh_window_seconds,
    probe_hard_deadline_seconds,
    run_monitor_cycle,
)
from backend.services.notification import NotificationService
from backend.services.stream_probe import ProbeResult


class RecordingManager:
    """Stands in for the WebSocket manager and records every broadcast."""

    def __init__(self):
        self.operator_messages = []
        self.camera_messages = []

    async def broadcast_to_all_operators(self, data):
        self.operator_messages.append(data)
        return 1

    async def broadcast_to_camera(self, camera_id, data):
        self.camera_messages.append((camera_id, data))
        return 1


def probe_returning(connected: bool, message: str = "Cannot open stream"):
    async def _probe(rtsp_url=None, device_id=None, timeout_seconds=5.0, capture_preview=False, measure_fps=False):
        return ProbeResult(connected=connected, message=message if not connected else "Stream connected")

    return _probe


@pytest.fixture
def notifier():
    manager = RecordingManager()
    return NotificationService(manager), manager


async def add_camera(session, camera_id="CAM-N-001", status=CameraStatus.ONLINE, region="north",
                     rtsp_url="rtsp://10.0.0.21:554/s", sector="Raxaul"):
    camera = Camera(camera_id=camera_id, name=f"Camera {camera_id}", zone_region=region,
                    sector_name=sector, status=status, rtsp_url=rtsp_url)
    session.add(camera)
    await session.commit()
    return camera


def test_detection_latency_budget_stays_within_threshold():
    """Heartbeat window + cycle spacing + probe deadline must fit inside the 30 s requirement.

    This covers both paths: a camera probed over RTSP, and one whose ML frame feed stops."""
    window = heartbeat_fresh_window_seconds()
    total = window + cycle_period_seconds() + probe_hard_deadline_seconds()
    assert total <= settings.CAMERA_OFFLINE_THRESHOLD_SECONDS, (
        f"worst-case detection {total}s exceeds the {settings.CAMERA_OFFLINE_THRESHOLD_SECONDS}s requirement"
    )
    assert cycle_period_seconds() >= 5
    assert window >= 0


async def test_dead_camera_goes_offline_and_raises_alert(session, notifier):
    service, manager = notifier
    await add_camera(session)

    result = await run_monitor_cycle(notifier=service, probe=probe_returning(False))
    assert result.checked == 1 and result.dead == 1
    assert len(result.went_offline) == 1

    camera = (await session.execute(select(Camera))).scalar_one()
    await session.refresh(camera)
    assert camera.status == CameraStatus.OFFLINE

    alert = (await session.execute(select(Alert))).scalar_one()
    assert alert.alert_type == AlertType.CAMERA_OFFLINE
    assert alert.risk_score == 60 and alert.risk_level == RiskLevel.SUSPICIOUS
    assert "camera_offline" in alert.risk_reasons[0]
    assert alert.track_id == 0

    offline_messages = [m for m in manager.operator_messages if m["type"] == "camera_offline"]
    assert len(offline_messages) == 1
    assert offline_messages[0]["camera_id"] == "CAM-N-001"
    assert offline_messages[0]["sound"] == "alert"
    assert offline_messages[0]["alert_id"] == alert.alert_id

    log = (await session.execute(
        select(AuditLog).where(AuditLog.action == "CAMERA_OFFLINE_DETECTED")
    )).scalar_one()
    assert log.username == "system:camera_monitor"
    assert log.new_value["status"] == "offline"


async def test_offline_camera_does_not_raise_duplicate_alerts(session, notifier):
    service, _ = notifier
    await add_camera(session)
    await run_monitor_cycle(notifier=service, probe=probe_returning(False))
    await run_monitor_cycle(notifier=service, probe=probe_returning(False))

    alerts = (await session.execute(select(Alert))).scalars().all()
    assert len(alerts) == 1, "an already-offline camera must not generate a new alert every cycle"


async def test_camera_recovery_is_detected_and_announced(session, notifier):
    service, manager = notifier
    await add_camera(session, status=CameraStatus.OFFLINE)

    result = await run_monitor_cycle(notifier=service, probe=probe_returning(True))
    assert len(result.came_online) == 1

    camera = (await session.execute(select(Camera))).scalar_one()
    await session.refresh(camera)
    assert camera.status == CameraStatus.ONLINE

    online = [m for m in manager.operator_messages if m["type"] == "camera_online"]
    assert len(online) == 1 and "back ONLINE" in online[0]["message"]
    assert (await session.execute(
        select(AuditLog).where(AuditLog.action == "CAMERA_ONLINE_DETECTED")
    )).scalar_one()


async def test_neighbouring_cameras_are_alerted(session, notifier):
    service, manager = notifier
    dead_url = "rtsp://10.0.0.21:554/dead"
    await add_camera(session, "CAM-N-001", rtsp_url=dead_url)
    await add_camera(session, "CAM-N-002", rtsp_url="rtsp://10.0.0.22:554/s")
    await add_camera(session, "CAM-N-003", rtsp_url="rtsp://10.0.0.23:554/s")
    await add_camera(session, "CAM-S-001", region="south", rtsp_url="rtsp://10.0.0.24:554/s")

    async def probe(rtsp_url=None, device_id=None, **kwargs):
        # only CAM-N-001 is dead; every other camera answers
        return ProbeResult(connected=rtsp_url != dead_url, message="Cannot open stream")

    result = await run_monitor_cycle(notifier=service, probe=probe)
    assert len(result.went_offline) == 1

    neighbour_ids = {camera_id for camera_id, message in manager.camera_messages
                     if message["type"] == "neighbor_offline_alert"}
    assert neighbour_ids == {"CAM-N-002", "CAM-N-003"}, "only online cameras in the same region are notified"
    assert "CAM-S-001" not in neighbour_ids


async def test_recent_ml_heartbeat_skips_the_probe(session, notifier):
    """A camera the ML pipeline is streaming is alive by definition; probing it would fight for the device."""
    service, _ = notifier
    await add_camera(session)
    runtime_state.record_frame("CAM-N-001", fps=15.0, people=1)

    probed = []

    async def probe(rtsp_url=None, device_id=None, **kwargs):
        probed.append(rtsp_url)
        return ProbeResult(connected=False, message="should not be called")

    result = await run_monitor_cycle(notifier=service, probe=probe)
    assert probed == []
    assert result.alive == 1 and result.dead == 0

    camera = (await session.execute(select(Camera))).scalar_one()
    await session.refresh(camera)
    assert camera.status == CameraStatus.ONLINE


async def test_stale_heartbeat_without_source_marks_offline(session, notifier):
    service, _ = notifier
    await add_camera(session, rtsp_url=None)          # e.g. a drone feed pushed over WebSocket
    runtime_state.record_frame("CAM-N-001", fps=15.0)
    runtime_state._heartbeats["CAM-N-001"].last_frame_monotonic = time.monotonic() - 120

    result = await run_monitor_cycle(notifier=service, probe=probe_returning(True))
    assert result.dead == 1
    alert = (await session.execute(select(Alert))).scalar_one()
    assert "heartbeat lost" in alert.risk_reasons[0]


async def test_camera_without_source_or_heartbeat_is_skipped(session, notifier):
    service, _ = notifier
    await add_camera(session, rtsp_url=None)

    result = await run_monitor_cycle(notifier=service, probe=probe_returning(False))
    assert result.skipped == 1 and result.checked == 0
    assert (await session.execute(select(Alert))).scalars().all() == []


async def test_cycle_survives_probe_errors(session, notifier):
    service, _ = notifier
    await add_camera(session)

    async def exploding_probe(**kwargs):
        raise RuntimeError("driver crashed")

    with pytest.raises(RuntimeError):
        await run_monitor_cycle(notifier=service, probe=exploding_probe)

    # The loop wrapper is what must absorb it: the camera is untouched and the next cycle still runs.
    result = await run_monitor_cycle(notifier=service, probe=probe_returning(False))
    assert result.dead == 1
