"""Hardware registry: status board, registration, connection testing and maintenance."""
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import visible_camera_ids_subquery
from backend.core.api_config import api_settings
from backend.core.dependencies import get_admin_user, get_current_active_user, get_supervisor_or_above
from backend.core.enums import HardwareStatus, HardwareType
from backend.core.security import MASK
from backend.database.crud import hardware_crud
from backend.database.database import get_db
from backend.models import Camera, HardwareRegistry
from backend.models.user import User
from backend.schemas.api import HardwareTestResponse, HardwareTypeSummary
from backend.schemas.hardware_registry import HardwareCreate, HardwareResponse, HardwareUpdate
from backend.services.audit import audit_action
from backend.services.stream_probe import probe_stream_async

logger = logging.getLogger("sih26187.hardware")

router = APIRouter(tags=["Hardware"])

STREAM_KEYS = ("rtsp_url", "stream_url", "url")
ENDPOINT_KEYS = ("api_endpoint", "endpoint", "http_url", "base_url")
HTTP_TIMEOUT_SECONDS = 5.0


def _config_value(config: Dict[str, Any], keys) -> Optional[str]:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _with_credentials(url: str, config: Dict[str, Any]) -> str:
    """Inject username/password from connection_config into a stream URL that has none."""
    username, password = config.get("username"), config.get("password")
    if not username or not password:
        return url
    parts = urlsplit(url)
    if parts.username or not parts.hostname:
        return url
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    netloc = f"{quote(str(username), safe='')}:{quote(str(password), safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _merge_secrets(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the stored secret when the client sends back the masked '***' value it was shown."""
    merged = dict(incoming)
    for key, value in incoming.items():
        if value == MASK and key in existing:
            merged[key] = existing[key]
        elif isinstance(value, dict) and isinstance(existing.get(key), dict):
            merged[key] = _merge_secrets(existing[key], value)
    return merged


async def _visible_hardware(db: AsyncSession, user: User, hardware_type: Optional[HardwareType] = None):
    query = select(HardwareRegistry)
    if hardware_type:
        query = query.where(HardwareRegistry.hardware_type == hardware_type)
    scope = visible_camera_ids_subquery(user)
    if scope is not None:
        query = query.where(or_(HardwareRegistry.camera_id.is_(None), HardwareRegistry.camera_id.in_(scope)))
    return (await db.execute(query.order_by(HardwareRegistry.hardware_type, HardwareRegistry.hardware_id))).scalars().all()


@router.get("/status", response_model=Dict[str, HardwareTypeSummary],
            summary="Hardware grouped by type (connection passwords masked)")
async def hardware_status(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> Dict[str, HardwareTypeSummary]:
    rows = await _visible_hardware(db, current_user)
    summary = {hardware_type.value: HardwareTypeSummary() for hardware_type in HardwareType}
    for item in rows:
        group = summary[item.hardware_type.value]
        group.total += 1
        setattr(group, item.status.value, getattr(group, item.status.value) + 1)
        group.items.append(HardwareResponse.model_validate(item))  # validator masks every secret

    await audit_action(db, "VIEW_HARDWARE_STATUS", request=request, user=current_user, table_name="hardware_registry",
                       new_value={"total": len(rows)})
    await db.commit()
    return summary


@router.post("/register", response_model=HardwareResponse, status_code=status.HTTP_201_CREATED,
             summary="Register hardware (admin only)")
async def register_hardware(
    request: Request,
    payload: HardwareCreate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> HardwareResponse:
    if payload.camera_id and await db.get(Camera, payload.camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Camera {payload.camera_id} not found")
    try:
        hardware = await hardware_crud.create(db, payload)
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Hardware could not be registered (constraint violation)") from exc

    await audit_action(db, "REGISTER_HARDWARE", request=request, user=current_user, table_name="hardware_registry",
                       record_id=hardware.hardware_id, new_value=hardware.to_dict())
    await db.commit()
    return HardwareResponse.model_validate(hardware)


@router.post("/{hardware_id}/test-connection", response_model=HardwareTestResponse,
             summary="Test a hardware connection")
async def test_hardware_connection(
    request: Request,
    hardware_id: int,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> HardwareTestResponse:
    hardware = await hardware_crud.get(db, hardware_id)
    if hardware is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hardware {hardware_id} not found")

    config = hardware.connection_config or {}
    stream_url = _config_value(config, STREAM_KEYS)
    device_id = config.get("device_id")
    endpoint = _config_value(config, ENDPOINT_KEYS)
    connected, latency_ms, message = False, 0, ""

    if stream_url or (device_id is not None and str(device_id).isdigit()):
        probe = await probe_stream_async(
            rtsp_url=_with_credentials(stream_url, config) if stream_url else None,
            device_id=str(device_id) if device_id is not None else None,
            timeout_seconds=api_settings.CAMERA_PROBE_TIMEOUT_SECONDS,
            capture_preview=False,
        )
        connected, latency_ms, message = probe.connected, probe.latency_ms, probe.message
    elif endpoint:
        started = time.perf_counter()
        auth = None
        if config.get("username") and config.get("password"):
            auth = (str(config["username"]), str(config["password"]))
        try:
            async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False, verify=True) as client:
                response = await client.get(endpoint, auth=auth)
            latency_ms = int((time.perf_counter() - started) * 1000)
            connected = response.status_code < 500
            message = f"HTTP {response.status_code} from endpoint"
        except httpx.HTTPError as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            message = f"HTTP request failed: {type(exc).__name__}"
    else:
        message = "connection_config has no rtsp_url/stream_url/device_id or api_endpoint to test"

    new_status = HardwareStatus.CONNECTED if connected else HardwareStatus.ERROR
    updates: Dict[str, Any] = {"status": new_status}
    if connected:
        updates["last_seen"] = datetime.now(timezone.utc)
    await hardware_crud.update(db, hardware, updates)

    await audit_action(db, "TEST_HARDWARE_CONNECTION", request=request, user=current_user,
                       table_name="hardware_registry", record_id=hardware_id,
                       new_value={"connected": connected, "latency_ms": latency_ms, "message": message},
                       status="success" if connected else "failure")
    await db.commit()
    return HardwareTestResponse(
        hardware_id=hardware_id, connected=connected, latency_ms=latency_ms, status=new_status, message=message,
    )


@router.patch("/{hardware_id}", response_model=HardwareResponse, summary="Update hardware (admin only)")
async def update_hardware(
    request: Request,
    hardware_id: int,
    payload: HardwareUpdate,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> HardwareResponse:
    hardware = await hardware_crud.get(db, hardware_id)
    if hardware is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hardware {hardware_id} not found")
    if payload.camera_id and await db.get(Camera, payload.camera_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Camera {payload.camera_id} not found")

    before = hardware.to_dict()
    updates = payload.model_dump(exclude_unset=True)
    if updates.get("connection_config") is not None:
        updates["connection_config"] = _merge_secrets(hardware.connection_config or {}, updates["connection_config"])
    hardware = await hardware_crud.update(db, hardware, updates)

    await audit_action(db, "UPDATE_HARDWARE", request=request, user=current_user, table_name="hardware_registry",
                       record_id=hardware_id, old_value=before, new_value=hardware.to_dict())
    await db.commit()
    return HardwareResponse.model_validate(hardware)


@router.delete("/{hardware_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete hardware (admin only)")
async def delete_hardware(
    request: Request,
    hardware_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    hardware = await hardware_crud.get(db, hardware_id)
    if hardware is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hardware {hardware_id} not found")

    before = hardware.to_dict()
    await db.delete(hardware)
    await db.flush()
    await audit_action(db, "DELETE_HARDWARE", request=request, user=current_user, table_name="hardware_registry",
                       record_id=hardware_id, old_value=before)
    await db.commit()
