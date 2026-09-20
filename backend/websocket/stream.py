"""Live camera WebSocket: operator viewing, ML frame ingestion and alert acknowledgement.

    ws://host/ws/{camera_id}?token=<JWT>

Incoming message types: ping, frame_data (ML pipeline only), acknowledge.
Outgoing: connected, pong, frame_update, frame_ack, alert_acknowledged, error, plus the
notification broadcasts (camera_offline, critical_alert, zone_update, ...).
"""
import asyncio
import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.api_config import api_settings
from backend.core.auth import resolve_user_from_token, user_can_access_camera
from backend.core.config import settings
from backend.core.enums import CameraStatus, RiskLevel
from backend.core.runtime import runtime_state
from backend.database.crud import alert_crud, evidence_crud, tracked_object_crud
from backend.database.database import get_db_context
from backend.models import Alert, Camera, Event, Evidence
from backend.models.alert import generate_alert_id
from backend.models.user import User
from backend.schemas.alert import AlertCreate, AlertResponse
from backend.schemas.api import WSAcknowledge, WSFrameData
from backend.core.enums import EvidenceType
from backend.services.audit import audit_action, audit_standalone
from backend.services.evidence_service import evidence_service
from backend.services.notification import notification_service
from backend.websocket.manager import manager

logger = logging.getLogger("sih26187.stream")

router = APIRouter(tags=["WebSocket"])

MAX_MESSAGE_BYTES = 24 * 1024 * 1024
CLOSE_POLICY_VIOLATION = 1008


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: Optional[datetime]) -> datetime:
    if value is None:
        return _now()
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _token_from(websocket: WebSocket, token: Optional[str]) -> Optional[str]:
    if token:
        return token
    header = websocket.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else None


async def _authenticate(websocket: WebSocket, camera_id: str, token: Optional[str]) -> Tuple[Optional[User], Optional[Camera], Optional[str]]:
    if not token:
        return None, None, "Missing token"
    async with get_db_context() as db:
        try:
            user, _claims = await resolve_user_from_token(db, token)
        except HTTPException as exc:
            return None, None, str(exc.detail)
        camera = await db.get(Camera, camera_id)
        if camera is None:
            return None, None, f"Camera {camera_id} not found"
        if not user_can_access_camera(user, camera.camera_id, camera.zone_region):
            return user, camera, "No access to this camera"
        db.expunge(user)
        db.expunge(camera)
    return user, camera, None


# --------------------------------------------------------------------------- frame ingestion

async def _persist_detections(db: AsyncSession, camera_id: str, payload: WSFrameData, stamp: datetime) -> int:
    if not payload.detections:
        return 0
    track_ids = {detection.track_id for detection in payload.detections}
    events: Dict[int, Event] = {
        event.track_id: event
        for event in (await db.execute(
            select(Event).where(Event.camera_id == camera_id, Event.track_id.in_(track_ids), Event.is_active.is_(True))
        )).scalars().all()
    }

    for detection in payload.detections:
        data = detection.model_dump()
        person_uuid = data.pop("person_uuid", None)
        data.update({
            "camera_id": camera_id,
            "timestamp": stamp,
            "zone_type": detection.zone_type.value if detection.zone_type else None,
            "direction": detection.direction.value,
            "risk_level": detection.risk_level.value,
        })
        if data.get("cx") is None and detection.bbox_x1 is not None and detection.bbox_x2 is not None:
            data["cx"] = (detection.bbox_x1 + detection.bbox_x2) // 2
        if data.get("cy") is None and detection.bbox_y1 is not None and detection.bbox_y2 is not None:
            data["cy"] = (detection.bbox_y1 + detection.bbox_y2) // 2
        await tracked_object_crud.create(db, data)

        event = events.get(detection.track_id)
        if event is None:
            event = Event(
                track_id=detection.track_id, camera_id=camera_id, person_uuid=person_uuid,
                object_class=detection.object_class, first_seen=stamp, last_seen=stamp,
            )
            db.add(event)
            await db.flush()
            events[detection.track_id] = event
        if person_uuid and not event.person_uuid:
            event.person_uuid = person_uuid
        if detection.cx is not None and detection.cy is not None:
            event.record_position(detection.cx, detection.cy, stamp, detection.zone_name, detection.risk_score)
        else:
            event.last_seen = max(event.last_seen, stamp)
            event.max_risk_score = max(event.max_risk_score or 0, detection.risk_score)

    # Tracks that stopped appearing are closed so /events/active reflects reality.
    await db.execute(
        update(Event)
        .where(
            Event.camera_id == camera_id,
            Event.is_active.is_(True),
            Event.last_seen < stamp - timedelta(seconds=settings.CAMERA_OFFLINE_THRESHOLD_SECONDS),
        )
        .values(is_active=False)
    )
    return len(payload.detections)


async def _decode_frame(payload: WSFrameData) -> Optional[bytes]:
    if not payload.frame:
        return None
    if len(payload.frame) > api_settings.WS_MAX_FRAME_BYTES * 4 // 3 + 128:
        raise ValueError("frame exceeds the configured size limit")
    try:
        frame_bytes = base64.b64decode(payload.frame, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"frame is not valid base64: {exc}")
    if len(frame_bytes) > api_settings.WS_MAX_FRAME_BYTES:
        raise ValueError("frame exceeds the configured size limit")
    if not frame_bytes.startswith(b"\xff\xd8\xff"):
        raise ValueError("frame must be a JPEG image")
    return frame_bytes


async def _persist_alerts(
    db: AsyncSession,
    camera: Camera,
    user: User,
    payload: WSFrameData,
    stamp: datetime,
    frame_bytes: Optional[bytes],
) -> Tuple[List[Alert], List[str]]:
    created: List[Alert] = []
    problems: List[str] = []
    for incoming in payload.alerts:
        alert_id = incoming.alert_id or generate_alert_id(stamp)
        if await alert_crud.get(db, alert_id) is not None:
            continue  # idempotent: the pipeline may resend the same alert on reconnect
        try:
            validated = AlertCreate(
                alert_id=alert_id,
                camera_id=camera.camera_id,
                track_id=incoming.track_id,
                person_uuid=incoming.person_uuid,
                alert_type=incoming.alert_type,
                risk_score=incoming.risk_score,
                risk_level=incoming.risk_level,
                risk_reasons=incoming.risk_reasons,
                zone_name=incoming.zone_name,
                zone_type=incoming.zone_type,
                timestamp=stamp,
            )
        except ValidationError as exc:
            problems.append(f"alert {alert_id}: {exc.errors(include_url=False)[0]['msg']}")
            continue

        data = validated.model_dump()
        data.update({"gps_lat": camera.gps_lat, "gps_lng": camera.gps_lng, "timestamp": stamp})
        alert = await alert_crud.create(db, data)

        snapshot_path, file_hash, file_size = None, None, None
        if frame_bytes:
            try:
                snapshot_path, file_hash = await evidence_service.save_snapshot(
                    frame_bytes, alert.alert_id, camera.camera_id
                )
                file_size = len(frame_bytes)
            except (ValueError, OSError, FileExistsError) as exc:
                problems.append(f"alert {alert.alert_id}: snapshot not stored ({exc})")
        elif incoming.snapshot_path and evidence_service.is_within_evidence_root(incoming.snapshot_path):
            candidate = Path(incoming.snapshot_path)
            if await asyncio.to_thread(candidate.is_file):
                snapshot_path = candidate.resolve().as_posix()
                file_hash = await asyncio.to_thread(evidence_service.compute_sha256, snapshot_path)
                file_size = await asyncio.to_thread(evidence_service.get_file_size, snapshot_path)

        if snapshot_path and file_hash:
            alert.snapshot_path = snapshot_path
            await evidence_crud.create(db, {
                "alert_id": alert.alert_id,
                "camera_id": camera.camera_id,
                "track_id": incoming.track_id,
                "evidence_type": EvidenceType.SNAPSHOT,
                "file_path": snapshot_path,
                "file_name": Path(snapshot_path).name,
                "file_size_bytes": file_size,
                "file_hash": file_hash,
                "gps_lat": camera.gps_lat,
                "gps_lng": camera.gps_lng,
            })

        if incoming.track_id is not None:
            event = (await db.execute(
                select(Event).where(
                    Event.camera_id == camera.camera_id,
                    Event.track_id == incoming.track_id,
                    Event.is_active.is_(True),
                )
            )).scalars().first()
            if event is not None:
                event.alert_count = (event.alert_count or 0) + 1
                event.max_risk_score = max(event.max_risk_score or 0, alert.risk_score)

        await audit_action(
            db, "ALERT_CREATED", user=user, table_name="alerts", record_id=alert.alert_id,
            new_value={"source": "ml_pipeline", "camera_id": camera.camera_id, "alert_type": alert.alert_type.value,
                       "risk_score": alert.risk_score, "risk_level": alert.risk_level.value,
                       "snapshot_stored": bool(snapshot_path)},
        )
        created.append(alert)
    return created, problems


async def handle_frame_data(camera: Camera, user: User, payload: WSFrameData) -> Dict[str, Any]:
    """Persist one frame's detections, alerts and evidence. Returns a summary for the sender."""
    stamp = _aware(payload.timestamp)
    frame_bytes = await _decode_frame(payload) if payload.alerts else None

    async with get_db_context() as db:
        camera_row = await db.get(Camera, camera.camera_id)
        if camera_row is None:
            raise ValueError(f"Camera {camera.camera_id} no longer exists")
        came_back_online = camera_row.status == CameraStatus.OFFLINE
        camera_row.last_seen = _now()
        if camera_row.status != CameraStatus.ONLINE:
            camera_row.status = CameraStatus.ONLINE

        saved = await _persist_detections(db, camera.camera_id, payload, stamp)
        alerts, problems = await _persist_alerts(db, camera_row, user, payload, stamp, frame_bytes)
        alert_payloads = [AlertResponse.model_validate(alert).model_dump(mode="json") for alert in alerts]
        critical = [alert for alert in alerts if alert.risk_level == RiskLevel.CRITICAL]
        critical_payloads = [
            (alert.alert_id, alert.risk_score, list(alert.risk_reasons or []), alert.alert_type.value)
            for alert in critical
        ]
        sector = camera_row.sector_name

    runtime_state.record_frame(
        camera.camera_id, payload.stats.fps, payload.stats.people_count,
        payload.stats.vehicle_count, payload.stats.animal_count,
    )
    if came_back_online:
        await notification_service.broadcast_camera_online(camera.camera_id, sector)
    for alert_id, risk_score, reasons, alert_type in critical_payloads:
        await notification_service.broadcast_critical_alert(alert_id, camera.camera_id, risk_score, reasons, alert_type)

    return {"detections_saved": saved, "alerts": alert_payloads, "problems": problems}


async def handle_acknowledge(camera: Camera, user: User, message: WSAcknowledge) -> Dict[str, Any]:
    async with get_db_context() as db:
        alert = await alert_crud.get(db, message.alert_id)
        if alert is None:
            raise ValueError(f"Alert {message.alert_id} not found")
        target = await db.get(Camera, alert.camera_id)
        if target is None or not user_can_access_camera(user, target.camera_id, target.zone_region):
            await audit_action(db, "ACKNOWLEDGE_ALERT", user=user, table_name="alerts", record_id=alert.alert_id,
                               new_value={"source": "websocket", "reason": "no_camera_access"}, status="denied")
            raise PermissionError("No access to this alert's camera")
        if alert.acknowledged:
            raise ValueError(f"Alert {message.alert_id} is already acknowledged")

        before = alert.to_dict()
        alert.acknowledge(user.user_id, message.false_alarm, message.notes)
        await db.flush()
        await audit_action(db, "ACKNOWLEDGE_ALERT", user=user, table_name="alerts", record_id=alert.alert_id,
                           old_value=before, new_value={**alert.to_dict(), "source": "websocket"})
        payload = AlertResponse.model_validate(alert).model_dump(mode="json")
        alert_camera_id = alert.camera_id
        false_alarm = alert.false_alarm

    await notification_service.broadcast_alert_acknowledged(
        alert_id=message.alert_id, acknowledged_by=str(user.user_id), camera_id=alert_camera_id,
        false_alarm=false_alarm, acknowledged_by_username=user.username,
    )
    return payload


# --------------------------------------------------------------------------- endpoint

@router.websocket("/{camera_id}")
async def camera_stream(websocket: WebSocket, camera_id: str, token: Optional[str] = Query(None)) -> None:
    camera_id = camera_id.upper()
    user, camera, error = await _authenticate(websocket, camera_id, _token_from(websocket, token))
    if error:
        await audit_standalone(
            "WS_CONNECT_DENIED", user=user, table_name="cameras", record_id=camera_id,
            new_value={"reason": error}, status="denied",
            ip_address=websocket.client.host if websocket.client else None,
        )
        await websocket.close(code=CLOSE_POLICY_VIOLATION, reason=error[:120])
        return

    user_id = str(user.user_id)
    may_ingest = user.role.value in api_settings.ws_ingest_roles
    await manager.connect(websocket, camera_id, user_id)
    logger.debug("WebSocket registered: camera=%s user=%s total_connections=%d",
                 camera_id, user_id, manager.get_connection_count())
    await audit_standalone("WS_CONNECT", user=user, table_name="cameras", record_id=camera_id,
                           new_value={"may_ingest": may_ingest},
                           ip_address=websocket.client.host if websocket.client else None)
    try:
        await websocket.send_json({
            "type": "connected",
            "camera_id": camera_id,
            "user_id": user_id,
            "role": user.role.value,
            "may_ingest_frames": may_ingest,
            "viewer_count": manager.get_camera_viewer_count(camera_id),
            "timestamp": _now().isoformat(),
        })

        while True:
            raw = await websocket.receive_text()
            if len(raw) > MAX_MESSAGE_BYTES:
                await websocket.send_json({"type": "error", "message": "Message too large"})
                continue
            try:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("payload must be a JSON object")
            except (json.JSONDecodeError, ValueError) as exc:
                await websocket.send_json({"type": "error", "message": f"Invalid JSON: {exc}"})
                continue

            message_type = message.get("type")
            try:
                if message_type == "ping":
                    await websocket.send_json({"type": "pong", "timestamp": _now().isoformat()})

                elif message_type == "frame_data":
                    if not may_ingest:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Role '{user.role.value}' may not publish frame_data",
                        })
                        continue
                    payload = WSFrameData.model_validate(message)
                    if payload.camera_id and payload.camera_id.upper() != camera_id:
                        await websocket.send_json({"type": "error", "message": "camera_id does not match this stream"})
                        continue
                    summary = await handle_frame_data(camera, user, payload)
                    broadcast = {
                        "type": "frame_update",
                        "camera_id": camera_id,
                        "frame": payload.frame,
                        "detections": [d.model_dump(mode="json") for d in payload.detections],
                        "alerts": summary["alerts"],
                        "stats": payload.stats.model_dump(mode="json"),
                        "timestamp": _aware(payload.timestamp).isoformat(),
                        "viewer_count": manager.get_camera_viewer_count(camera_id),
                        "server_timestamp": _now().isoformat(),
                    }
                    await manager.broadcast_to_camera(camera_id, broadcast)
                    await websocket.send_json({
                        "type": "frame_ack",
                        "detections_saved": summary["detections_saved"],
                        "alerts_created": len(summary["alerts"]),
                        "problems": summary["problems"],
                        "timestamp": _now().isoformat(),
                    })

                elif message_type == "acknowledge":
                    acknowledgement = WSAcknowledge.model_validate(message)
                    alert_payload = await handle_acknowledge(camera, user, acknowledgement)
                    await websocket.send_json({
                        "type": "acknowledge_ok",
                        "alert": alert_payload,
                        "timestamp": _now().isoformat(),
                    })

                else:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Unknown message type '{message_type}'",
                    })

            except ValidationError as exc:
                await websocket.send_json({
                    "type": "error",
                    "message": "Payload validation failed",
                    "errors": exc.errors(include_url=False)[:5],
                })
            except PermissionError as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except ValueError as exc:
                await websocket.send_json({"type": "error", "message": str(exc)})
            except Exception:
                logger.exception("WebSocket message handling failed (camera=%s user=%s)", camera_id, user_id)
                await websocket.send_json({"type": "error", "message": "Internal error while processing message"})

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: camera=%s user=%s", camera_id, user.username)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("WebSocket connection error (camera=%s user=%s)", camera_id, user_id)
    finally:
        await manager.disconnect(websocket, camera_id, user_id)
        await audit_standalone("WS_DISCONNECT", user=user, table_name="cameras", record_id=camera_id,
                               ip_address=websocket.client.host if websocket.client else None)
