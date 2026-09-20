"""Background camera liveness monitor.

Liveness evidence, in priority order:
1. a recent frame_data heartbeat from the ML pipeline (no probe needed — probing a webcam the pipeline
   already holds open would fail on Windows and raise false offline alerts);
2. an OpenCV probe of rtsp_url / numeric device_id;
3. cameras with no stream source that previously sent frames are marked offline when the heartbeat goes stale.

Detection latency guarantee: heartbeat window + cycle spacing + probe deadline is kept at or below
CAMERA_OFFLINE_THRESHOLD_SECONDS (30 s), so a camera is always flagged within 30 s of its last sign of life.
See heartbeat_fresh_window_seconds() for the arithmetic.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

from sqlalchemy import select

from backend.core.api_config import api_settings
from backend.core.config import settings
from backend.core.enums import AlertType, CameraStatus, RiskLevel
from backend.core.runtime import runtime_state
from backend.database.crud import alert_crud, camera_crud
from backend.database.database import get_db_context
from backend.models import Alert, Camera
from backend.models.alert import generate_alert_id
from backend.services.audit import audit_action
from backend.services.notification import NotificationService, notification_service
from backend.services.stream_probe import ProbeResult, is_device_index, probe_stream_async
from backend.websocket.manager import ConnectionManager, manager

logger = logging.getLogger("sih26187.camera_monitor")

MONITOR_USERNAME = "system:camera_monitor"
OFFLINE_RISK_SCORE = 60
PAGE_SIZE = 500

ProbeFn = Callable[..., Awaitable[ProbeResult]]


def detection_budget_seconds() -> float:
    return float(min(settings.HEALTH_CHECK_INTERVAL_SECONDS, settings.CAMERA_OFFLINE_THRESHOLD_SECONDS))


def probe_hard_deadline_seconds() -> float:
    """Longest a single probe can take (open timeout + read timeout + margin)."""
    return api_settings.CAMERA_PROBE_TIMEOUT_SECONDS * 2 + 1.0


def cycle_period_seconds() -> float:
    """Start-to-start spacing of monitor cycles."""
    return max(5.0, (detection_budget_seconds() - probe_hard_deadline_seconds()) // 2)


def heartbeat_fresh_window_seconds() -> float:
    """How stale an ML frame heartbeat may be and still count as proof of life.

    The three delays add up to the worst case for spotting a dead camera:
        heartbeat window + cycle spacing + probe deadline <= CAMERA_OFFLINE_THRESHOLD_SECONDS.
    A running pipeline sends frames continuously, so its cameras are never probed in practice; once the
    frames stop, the next cycle falls back to probing well inside the 30 s budget.
    """
    return max(0.0, detection_budget_seconds() - cycle_period_seconds() - probe_hard_deadline_seconds())


@dataclass
class CameraSnapshot:
    camera_id: str
    rtsp_url: Optional[str]
    device_id: Optional[str]


@dataclass
class OfflineTransition:
    camera_id: str
    sector_name: Optional[str]
    zone_region: str
    alert_id: str
    reason: str
    neighbor_ids: List[str] = field(default_factory=list)


@dataclass
class OnlineTransition:
    camera_id: str
    sector_name: Optional[str]


@dataclass
class MonitorCycleResult:
    checked: int = 0
    alive: int = 0
    dead: int = 0
    skipped: int = 0
    went_offline: List[OfflineTransition] = field(default_factory=list)
    came_online: List[OnlineTransition] = field(default_factory=list)
    duration_seconds: float = 0.0


async def create_offline_alert(db, camera: Camera, reason: str) -> Alert:
    return await alert_crud.create(db, {
        "alert_id": generate_alert_id(),
        "camera_id": camera.camera_id,
        "track_id": 0,
        "alert_type": AlertType.CAMERA_OFFLINE,
        "risk_score": OFFLINE_RISK_SCORE,
        "risk_level": RiskLevel(settings.get_risk_level(OFFLINE_RISK_SCORE)),
        "risk_reasons": [f"camera_offline: {reason}"],
        "zone_name": camera.sector_name,
        "gps_lat": camera.gps_lat,
        "gps_lng": camera.gps_lng,
        "timestamp": datetime.now(timezone.utc),
    })


async def neighbor_camera_ids(db, camera: Camera) -> List[str]:
    rows = await db.execute(
        select(Camera.camera_id).where(
            Camera.zone_region == camera.zone_region,
            Camera.status == CameraStatus.ONLINE,
            Camera.camera_id != camera.camera_id,
        )
    )
    return [row[0] for row in rows]


async def mark_camera_offline(db, camera: Camera, reason: str, actor=None, request=None) -> OfflineTransition:
    """Set status offline, raise a camera_offline alert and audit it (caller commits, then notifies)."""
    previous = camera.status
    await camera_crud.update(db, camera, {"status": CameraStatus.OFFLINE})
    alert = await create_offline_alert(db, camera, reason)
    await audit_action(
        db,
        "CAMERA_OFFLINE_DETECTED" if actor is None else "CAMERA_MARKED_OFFLINE",
        request=request,
        user=actor,
        username=MONITOR_USERNAME if actor is None else None,
        table_name="cameras",
        record_id=camera.camera_id,
        old_value={"status": getattr(previous, "value", previous)},
        new_value={"status": "offline", "alert_id": alert.alert_id, "reason": reason},
    )
    return OfflineTransition(
        camera_id=camera.camera_id,
        sector_name=camera.sector_name,
        zone_region=camera.zone_region.value,
        alert_id=alert.alert_id,
        reason=reason,
        neighbor_ids=await neighbor_camera_ids(db, camera),
    )


async def notify_offline(transition: OfflineTransition, notifier: NotificationService) -> None:
    await notifier.broadcast_camera_offline(
        transition.camera_id, transition.sector_name, transition.zone_region, transition.alert_id, transition.reason
    )
    for neighbor_id in transition.neighbor_ids:
        await notifier.broadcast_neighbor_offline(neighbor_id, transition.camera_id, transition.sector_name)


async def activate_neighboring_cameras(db, offline_camera: Camera, ws_manager: ConnectionManager) -> int:
    """Tell viewers of every online camera in the same region to increase attention. Returns rooms notified."""
    notifier = NotificationService(ws_manager)
    notified = 0
    for neighbor_id in await neighbor_camera_ids(db, offline_camera):
        await notifier.broadcast_neighbor_offline(neighbor_id, offline_camera.camera_id, offline_camera.sector_name)
        notified += 1
    return notified


async def _load_snapshots(db_factory) -> List[CameraSnapshot]:
    snapshots: List[CameraSnapshot] = []
    async with db_factory() as db:
        skip = 0
        while True:
            page = await camera_crud.get_multi(db, skip=skip, limit=PAGE_SIZE)
            snapshots.extend(CameraSnapshot(c.camera_id, c.rtsp_url, c.device_id) for c in page)
            if len(page) < PAGE_SIZE:
                break
            skip += PAGE_SIZE
    return snapshots


async def _assess(snapshot: CameraSnapshot, probe: ProbeFn, semaphore: asyncio.Semaphore) -> Optional[tuple]:
    """Returns (alive: bool, reason) or None when the camera cannot be assessed."""
    since_frame = runtime_state.seconds_since_frame(snapshot.camera_id)
    if since_frame is not None and since_frame <= heartbeat_fresh_window_seconds():
        return True, "ml_heartbeat"

    if snapshot.rtsp_url or is_device_index(snapshot.device_id):
        async with semaphore:
            result = await probe(
                rtsp_url=None if is_device_index(snapshot.device_id) else snapshot.rtsp_url,
                device_id=snapshot.device_id if is_device_index(snapshot.device_id) else None,
                timeout_seconds=api_settings.CAMERA_PROBE_TIMEOUT_SECONDS,
                capture_preview=False,
            )
        return (True, "probe_ok") if result.connected else (False, result.message)

    if since_frame is not None:
        return False, f"ML frame heartbeat lost for {int(since_frame)}s"
    return None


async def run_monitor_cycle(
    db_factory=get_db_context,
    notifier: NotificationService = notification_service,
    probe: ProbeFn = probe_stream_async,
) -> MonitorCycleResult:
    started = time.monotonic()
    result = MonitorCycleResult()
    snapshots = await _load_snapshots(db_factory)
    semaphore = asyncio.Semaphore(max(1, api_settings.CAMERA_PROBE_CONCURRENCY))
    assessments = await asyncio.gather(*(_assess(s, probe, semaphore) for s in snapshots))

    now = datetime.now(timezone.utc)
    async with db_factory() as db:
        for snapshot, assessment in zip(snapshots, assessments):
            if assessment is None:
                result.skipped += 1
                continue
            result.checked += 1
            alive, reason = assessment
            camera = await db.get(Camera, snapshot.camera_id, with_for_update=True)
            if camera is None:  # deleted while probing
                continue
            if alive:
                result.alive += 1
                if camera.status == CameraStatus.OFFLINE:
                    await camera_crud.update(db, camera, {"status": CameraStatus.ONLINE, "last_seen": now})
                    await audit_action(
                        db, "CAMERA_ONLINE_DETECTED", username=MONITOR_USERNAME, table_name="cameras",
                        record_id=camera.camera_id, old_value={"status": "offline"},
                        new_value={"status": "online", "evidence": reason},
                    )
                    result.came_online.append(OnlineTransition(camera.camera_id, camera.sector_name))
                elif camera.status == CameraStatus.DEGRADED and reason == "probe_ok":
                    await camera_crud.update(db, camera, {"status": CameraStatus.ONLINE, "last_seen": now})
                else:
                    await camera_crud.update(db, camera, {"last_seen": now})
            else:
                result.dead += 1
                if camera.status != CameraStatus.OFFLINE:
                    result.went_offline.append(await mark_camera_offline(db, camera, reason))
        # get_db_context commits here, before any notification is sent.

    for transition in result.went_offline:
        logger.warning("Camera %s OFFLINE (%s) alert=%s", transition.camera_id, transition.reason, transition.alert_id)
        await notify_offline(transition, notifier)
    for transition in result.came_online:
        logger.info("Camera %s back ONLINE", transition.camera_id)
        await notifier.broadcast_camera_online(transition.camera_id, transition.sector_name)

    result.duration_seconds = round(time.monotonic() - started, 3)
    return result


async def camera_monitor_loop(
    db_factory=get_db_context,
    notification_service: NotificationService = notification_service,
    ws_manager: ConnectionManager = manager,
    probe: ProbeFn = probe_stream_async,
) -> None:
    period = cycle_period_seconds()
    logger.info("Camera monitor started: cycle every %.0fs, probe deadline %.0fs", period, probe_hard_deadline_seconds())
    notifier = notification_service if notification_service.ws_manager is ws_manager else NotificationService(ws_manager)
    while True:
        cycle_start = time.monotonic()
        try:
            outcome = await run_monitor_cycle(db_factory, notifier, probe)
            logger.debug(
                "Monitor cycle: checked=%d alive=%d dead=%d skipped=%d in %.2fs",
                outcome.checked, outcome.alive, outcome.dead, outcome.skipped, outcome.duration_seconds,
            )
        except asyncio.CancelledError:
            logger.info("Camera monitor stopped")
            raise
        except Exception:
            logger.exception("Camera monitor cycle failed")
        await asyncio.sleep(max(1.0, period - (time.monotonic() - cycle_start)))


def monitor_status(task: Optional[asyncio.Task]) -> str:
    if not api_settings.CAMERA_MONITOR_ENABLED:
        return "disabled"
    return "running" if task is not None and not task.done() else "stopped"


__all__ = [
    "camera_monitor_loop",
    "run_monitor_cycle",
    "mark_camera_offline",
    "notify_offline",
    "activate_neighboring_cameras",
    "cycle_period_seconds",
    "probe_hard_deadline_seconds",
    "heartbeat_fresh_window_seconds",
    "detection_budget_seconds",
    "monitor_status",
]
