"""Camera registration, listing, status control and stream testing."""
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import restrict_to_visible_cameras
from backend.core.api_config import api_settings
from backend.core.auth import ensure_camera_access, require_camera_access
from backend.core.dependencies import get_admin_user, get_current_active_user, get_supervisor_or_above
from backend.core.enums import CameraStatus, CameraType, UserRole, ZoneRegion
from backend.core.runtime import runtime_state
from backend.database.crud import camera_crud
from backend.database.database import get_db
from backend.models import Alert, Camera, Event, Evidence
from backend.models.user import User
from backend.schemas.alert import AlertResponse
from backend.schemas.api import (
    CameraDeleteResponse,
    CameraDetailResponse,
    CameraEditRequest,
    CameraListWithAlertsResponse,
    CameraRegisterRequest,
    CameraRegisterResponse,
    CameraStatusResponse,
    CameraStatusUpdate,
    CameraWithAlertCount,
    HostLocationResponse,
    StreamTestResponse,
)
from backend.schemas.camera import CameraResponse
from backend.services.audit import audit_action
from backend.services.camera_monitor import mark_camera_offline, notify_offline
from backend.services.host_location import HostLocationUnavailable, read_host_location
from backend.services.notification import notification_service
from backend.services.stream_probe import is_device_index, probe_stream_async

router = APIRouter(tags=["Cameras"])

REGION_LETTER = {
    ZoneRegion.NORTH.value: "N",
    ZoneRegion.SOUTH.value: "S",
    ZoneRegion.EAST.value: "E",
    ZoneRegion.WEST.value: "W",
}
RECENT_ALERT_LIMIT = 5


async def next_camera_id(db: AsyncSession, region: str) -> str:
    """CAM-{N|S|E|W}-{NNN}. A transaction-scoped advisory lock serialises concurrent registrations."""
    letter = REGION_LETTER[getattr(region, "value", region)]
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"sih26187:camera_id:{letter}"})
    rows = await db.execute(select(Camera.camera_id).where(Camera.camera_id.like(f"CAM-{letter}-%")))
    pattern = re.compile(rf"^CAM-{letter}-(\d+)$")
    highest = 0
    for (camera_id,) in rows:
        match = pattern.match(camera_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return f"CAM-{letter}-{highest + 1:03d}"


@router.post(
    "/register",
    response_model=CameraRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a camera (server generates the camera_id and probes the stream)",
)
async def register_camera(
    request: Request,
    payload: CameraRegisterRequest,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> CameraRegisterResponse:
    region = payload.zone_region.value
    if current_user.role != UserRole.ADMIN and region not in (current_user.zone_access or []):
        await audit_action(
            db, "REGISTER_CAMERA", request=request, user=current_user, table_name="cameras",
            new_value={"zone_region": region, "reason": "region_not_granted"}, status="denied", commit=True,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"No access to region {region}")

    # Probe before taking the advisory lock so a slow stream never holds the id sequence lock.
    probe = await probe_stream_async(
        rtsp_url=payload.rtsp_url,
        device_id=payload.device_id,
        timeout_seconds=api_settings.CAMERA_PROBE_TIMEOUT_SECONDS,
        capture_preview=True,
    )
    has_source = bool(payload.rtsp_url) or is_device_index(payload.device_id)
    camera_status = CameraStatus.ONLINE if probe.connected else CameraStatus.DEGRADED
    if not has_source:
        message = "Camera registered without a stream source; status is degraded until a source is configured"
    elif probe.connected:
        message = f"Stream verified ({probe.resolution or 'unknown resolution'})"
    else:
        message = f"Stream could not be verified: {probe.message}. Camera saved as degraded"

    camera_id = await next_camera_id(db, region)
    data = payload.model_dump()
    data.update({"camera_id": camera_id, "status": camera_status, "last_seen": datetime.now(timezone.utc)})
    camera = await camera_crud.create(db, data)

    await audit_action(
        db, "REGISTER_CAMERA", request=request, user=current_user, table_name="cameras",
        record_id=camera.camera_id, new_value=camera.to_dict(),
    )
    await db.commit()
    return CameraRegisterResponse(
        **CameraResponse.model_validate(camera).model_dump(),
        connection_ok=probe.connected,
        preview_frame=probe.frame_jpeg_b64,
        message=message,
    )


@router.get(
    "",
    response_model=CameraListWithAlertsResponse,
    summary="List cameras (most unacknowledged alerts first)",
)
async def list_cameras(
    request: Request,
    zone_region: Optional[ZoneRegion] = Query(None),
    status_filter: Optional[CameraStatus] = Query(None, alias="status"),
    camera_type: Optional[CameraType] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CameraListWithAlertsResponse:
    alert_counts = (
        select(Alert.camera_id.label("camera_id"), func.count().label("active_alerts"))
        .where(Alert.acknowledged.is_(False))
        .group_by(Alert.camera_id)
        .subquery()
    )
    active_alerts = func.coalesce(alert_counts.c.active_alerts, 0)
    query = select(Camera, active_alerts).outerjoin(alert_counts, alert_counts.c.camera_id == Camera.camera_id)
    count_query = select(func.count()).select_from(Camera)

    conditions = []
    if zone_region:
        conditions.append(Camera.zone_region == zone_region)
    if status_filter:
        conditions.append(Camera.status == status_filter)
    if camera_type:
        conditions.append(Camera.camera_type == camera_type)
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)

    query = restrict_to_visible_cameras(query, Camera.camera_id, current_user)
    count_query = restrict_to_visible_cameras(count_query, Camera.camera_id, current_user)

    rows = (await db.execute(query.order_by(active_alerts.desc(), Camera.camera_id).offset(skip).limit(limit))).all()
    total = int((await db.execute(count_query)).scalar() or 0)
    items = [
        CameraWithAlertCount(**CameraResponse.model_validate(camera).model_dump(), active_alert_count=int(count))
        for camera, count in rows
    ]

    await audit_action(
        db, "LIST_CAMERAS", request=request, user=current_user, table_name="cameras",
        new_value={
            "zone_region": zone_region.value if zone_region else None,
            "status": status_filter.value if status_filter else None,
            "camera_type": camera_type.value if camera_type else None,
            "returned": len(items),
            "total": total,
        },
    )
    await db.commit()
    return CameraListWithAlertsResponse(items=items, total=total)


@router.get(
    "/host-location",
    response_model=HostLocationResponse,
    summary="Position of the console host from the Windows location service",
)
async def host_location(
    request: Request,
    refresh: bool = Query(False, description="bypass the one-minute cache"),
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> HostLocationResponse:
    """Cameras attached to this machine (webcam / USB device index) are located here. Used when the
    operator's browser has no geolocation (blocked permission, embedded browser, no provider)."""
    try:
        fix = await read_host_location(force=refresh)
    except HostLocationUnavailable as exc:
        await audit_action(
            db, "READ_HOST_LOCATION", request=request, user=current_user, status="failed",
            new_value={"reason": str(exc)}, commit=True,
        )
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    await audit_action(
        db, "READ_HOST_LOCATION", request=request, user=current_user,
        new_value={"source": fix.source, "accuracy_m": fix.accuracy_m},
    )
    await db.commit()
    return HostLocationResponse(
        lat=fix.lat, lng=fix.lng, accuracy_m=fix.accuracy_m, source=fix.source, captured_at=fix.captured_at,
    )


@router.get("/{camera_id}", response_model=CameraDetailResponse, summary="Camera detail with recent alerts")
async def get_camera(
    request: Request,
    camera: Camera = Depends(require_camera_access),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> CameraDetailResponse:
    active_alert_count = int((await db.execute(
        select(func.count()).select_from(Alert)
        .where(Alert.camera_id == camera.camera_id, Alert.acknowledged.is_(False))
    )).scalar() or 0)
    recent = (await db.execute(
        select(Alert).where(Alert.camera_id == camera.camera_id)
        .order_by(Alert.timestamp.desc()).limit(RECENT_ALERT_LIMIT)
    )).scalars().all()

    await audit_action(
        db, "VIEW_CAMERA", request=request, user=current_user, table_name="cameras", record_id=camera.camera_id,
    )
    await db.commit()
    return CameraDetailResponse(
        **CameraResponse.model_validate(camera).model_dump(),
        active_alert_count=active_alert_count,
        viewer_count=notification_service.ws_manager.get_camera_viewer_count(camera.camera_id),
        recent_alerts=[AlertResponse.model_validate(alert) for alert in recent],
    )


@router.patch("/{camera_id}/status", response_model=CameraStatusResponse, summary="Set camera status")
async def update_camera_status(
    request: Request,
    camera_id: str,
    payload: CameraStatusUpdate,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> CameraStatusResponse:
    camera = await ensure_camera_access(request, db, current_user, camera_id.upper())
    previous = camera.status
    transition = None

    if payload.status == CameraStatus.OFFLINE and previous != CameraStatus.OFFLINE:
        transition = await mark_camera_offline(
            db, camera, "marked offline by operator", actor=current_user, request=request
        )
    else:
        await camera_crud.update(db, camera, {"status": payload.status})

    await audit_action(
        db, "UPDATE_CAMERA_STATUS", request=request, user=current_user, table_name="cameras",
        record_id=camera.camera_id,
        old_value={"status": previous.value}, new_value={"status": payload.status.value},
    )
    await db.commit()

    if transition is not None:
        await notify_offline(transition, notification_service)
    elif payload.status == CameraStatus.ONLINE and previous == CameraStatus.OFFLINE:
        await notification_service.broadcast_camera_online(camera.camera_id, camera.sector_name)

    return CameraStatusResponse(
        **CameraResponse.model_validate(camera).model_dump(),
        previous_status=previous,
        offline_alert_id=transition.alert_id if transition else None,
    )


def _audit_value(value):
    """JSON-safe audit representation (enums by value, NUMERIC GPS columns as float)."""
    value = getattr(value, "value", value)
    return float(value) if isinstance(value, Decimal) else value


@router.patch("/{camera_id}", response_model=CameraResponse, summary="Edit camera metadata (name, location, GPS, type, region)")
async def edit_camera(
    request: Request,
    camera_id: str,
    payload: CameraEditRequest,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> CameraResponse:
    camera = await ensure_camera_access(request, db, current_user, camera_id.upper())
    changes = payload.model_dump(exclude_unset=True)
    for key in ("name", "location_name", "sector_name"):
        if isinstance(changes.get(key), str) and key != "name" and not changes[key]:
            changes[key] = None  # an emptied optional text field clears it
    before = {key: _audit_value(getattr(camera, key)) for key in changes}
    await camera_crud.update(db, camera, changes, exclude_none=False)  # explicit nulls clear GPS/text
    after = {key: _audit_value(value) for key, value in changes.items()}
    await audit_action(
        db, "EDIT_CAMERA", request=request, user=current_user, table_name="cameras",
        record_id=camera.camera_id, old_value=before, new_value=after,
    )
    await db.commit()
    await db.refresh(camera)
    return CameraResponse.model_validate(camera)


@router.delete("/{camera_id}", response_model=CameraDeleteResponse, summary="Delete a camera (admin only)")
async def delete_camera(
    request: Request,
    camera_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> CameraDeleteResponse:
    camera_id = camera_id.upper()
    camera = await db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Camera {camera_id} not found")

    evidence_count = int((await db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.camera_id == camera_id)
    )).scalar() or 0)
    if evidence_count:
        await audit_action(
            db, "DELETE_CAMERA", request=request, user=current_user, table_name="cameras", record_id=camera_id,
            new_value={"reason": "evidence_exists", "evidence_count": evidence_count}, status="failure", commit=True,
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Cannot delete camera with existing evidence ({evidence_count} records)"
        )

    alert_count = int((await db.execute(
        select(func.count()).select_from(Alert).where(Alert.camera_id == camera_id)
    )).scalar() or 0)
    event_count = int((await db.execute(
        select(func.count()).select_from(Event).where(Event.camera_id == camera_id)
    )).scalar() or 0)
    history = alert_count + event_count

    before = camera.to_dict(include_secrets=True)
    if history:
        # Alerts and events are retained (FK RESTRICT), so decommission instead of deleting, and drop the
        # stream source so the monitor stops probing a camera that is no longer in service.
        await camera_crud.update(db, camera, {"status": CameraStatus.OFFLINE})
        camera.rtsp_url = None
        camera.device_id = None
        await db.flush()
        deletion = "soft"
        message = f"Camera decommissioned (status=offline); {history} alert/event records retained"
    else:
        await db.delete(camera)
        await db.flush()
        deletion = "hard"
        message = "Camera deleted"

    runtime_state.forget(camera_id)
    await audit_action(
        db, "DELETE_CAMERA", request=request, user=current_user, table_name="cameras", record_id=camera_id,
        old_value=before, new_value={"deletion": deletion},
    )
    await db.commit()
    return CameraDeleteResponse(camera_id=camera_id, deletion=deletion, message=message)


@router.get("/{camera_id}/test", response_model=StreamTestResponse, summary="Test the camera stream")
async def test_camera(
    request: Request,
    camera_id: str,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> StreamTestResponse:
    camera = await ensure_camera_access(request, db, current_user, camera_id.upper())
    probe = await probe_stream_async(
        rtsp_url=camera.rtsp_url,
        device_id=camera.device_id,
        timeout_seconds=api_settings.CAMERA_PROBE_TIMEOUT_SECONDS,
        capture_preview=True,
        measure_fps=True,
    )
    if probe.connected:
        updates = {"last_seen": datetime.now(timezone.utc)}
        if camera.status != CameraStatus.ONLINE:
            updates["status"] = CameraStatus.ONLINE
        await camera_crud.update(db, camera, updates)

    await audit_action(
        db, "TEST_CAMERA", request=request, user=current_user, table_name="cameras", record_id=camera.camera_id,
        new_value={
            "connected": probe.connected, "resolution": probe.resolution,
            "fps": probe.fps, "message": probe.message,
        },
    )
    await db.commit()
    return StreamTestResponse(
        camera_id=camera.camera_id,
        connected=probe.connected,
        fps=probe.fps,
        resolution=probe.resolution,
        frame_preview=probe.frame_jpeg_b64,
        latency_ms=probe.latency_ms,
        message=probe.message,
    )
