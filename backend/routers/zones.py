"""Detection zone management (polygons, risk bonus policy, live broadcast to viewers)."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import normalize_camera_id, restrict_to_visible_cameras
from backend.core.auth import ensure_camera_access, require_camera_access
from backend.core.config import settings
from backend.core.dependencies import get_current_active_user, get_supervisor_or_above
from backend.core.enums import ZoneType
from backend.database.crud import camera_crud, zone_crud
from backend.database.database import get_db
from backend.models import Camera, Zone
from backend.models.user import User
from backend.schemas.api import MessageResponse
from backend.schemas.zone import ZoneCreate, ZoneListResponse, ZoneResponse, ZoneUpdate
from backend.services.audit import audit_action
from backend.services.geo_calibration import CalibrationError, project_polygon
from backend.services.notification import notification_service

router = APIRouter(tags=["Zones"])


def risk_bonus_for(zone_type: ZoneType) -> int:
    """Risk bonus is policy, derived from the zone type, never taken from the request body."""
    return settings.ZONE_RISK_BONUS[zone_type.value]


def validate_integer_polygon(polygon: List[List[float]]) -> List[List[int]]:
    """Polygon points are pixel coordinates: at least 3, whole numbers, non-negative."""
    if len(polygon) < 3:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "polygon requires at least 3 points")
    cleaned = []
    for index, point in enumerate(polygon):
        if len(point) != 2:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"polygon point {index} must be [x, y]")
        x, y = float(point[0]), float(point[1])
        if not x.is_integer() or not y.is_integer():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"polygon point {index} must use integer pixel coordinates")
        if x < 0 or y < 0:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"polygon point {index} must be non-negative")
        cleaned.append([int(x), int(y)])
    return cleaned


async def project_from_map(db, camera_id: str, geo_polygon: List[List[float]]) -> List[List[int]]:
    """Map polygon ([[lat, lng], ...]) → camera pixels, through that camera's ground-plane calibration."""
    camera = await camera_crud.get(db, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Camera {camera_id} not found")
    if not camera.calibration:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{camera_id} is not calibrated against the map yet. Calibrate it (4 landmarks) before drawing "
            "zones on the map, or draw the zone on the camera image instead.",
        )
    try:
        return project_polygon(camera.calibration, geo_polygon)
    except CalibrationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("", response_model=ZoneListResponse, summary="List zones")
async def list_zones(
    request: Request,
    camera_id: Optional[str] = Query(None, max_length=20),
    zone_type: Optional[ZoneType] = Query(None),
    is_active: Optional[bool] = Query(True),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ZoneListResponse:
    conditions = []
    camera_id = normalize_camera_id(camera_id)
    if camera_id:
        conditions.append(Zone.camera_id == camera_id)
    if zone_type:
        conditions.append(Zone.zone_type == zone_type)
    if is_active is not None:
        conditions.append(Zone.is_active.is_(is_active))

    query = select(Zone)
    count_query = select(func.count()).select_from(Zone)
    for condition in conditions:
        query, count_query = query.where(condition), count_query.where(condition)
    query = restrict_to_visible_cameras(query, Zone.camera_id, current_user)
    count_query = restrict_to_visible_cameras(count_query, Zone.camera_id, current_user)

    rows = (await db.execute(query.order_by(Zone.camera_id, Zone.zone_id).offset(skip).limit(limit))).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)

    await audit_action(db, "LIST_ZONES", request=request, user=current_user, table_name="zones",
                       new_value={"camera_id": camera_id, "returned": len(rows), "total": total})
    await db.commit()
    return ZoneListResponse(items=[ZoneResponse.model_validate(zone) for zone in rows], total=total)


@router.get("/{camera_id}", response_model=ZoneListResponse, summary="Active zones for one camera")
async def zones_for_camera(
    request: Request,
    camera: Camera = Depends(require_camera_access),
    include_inactive: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> ZoneListResponse:
    query = select(Zone).where(Zone.camera_id == camera.camera_id)
    if not include_inactive:
        query = query.where(Zone.is_active.is_(True))
    rows = (await db.execute(query.order_by(Zone.zone_id))).scalars().all()

    await audit_action(db, "VIEW_CAMERA_ZONES", request=request, user=current_user, table_name="zones",
                       record_id=camera.camera_id, new_value={"zones": len(rows)})
    await db.commit()
    return ZoneListResponse(items=[ZoneResponse.model_validate(zone) for zone in rows], total=len(rows))


@router.post("", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED, summary="Create a zone")
async def create_zone(
    request: Request,
    payload: ZoneCreate,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> ZoneResponse:
    await ensure_camera_access(request, db, current_user, payload.camera_id)
    data = payload.model_dump()
    if payload.geo_polygon is not None:
        data["polygon"] = await project_from_map(db, payload.camera_id, payload.geo_polygon)
    else:
        data["polygon"] = validate_integer_polygon(payload.polygon)
    data["risk_bonus"] = risk_bonus_for(payload.zone_type)

    zone = await zone_crud.create(db, data)
    await audit_action(db, "CREATE_ZONE", request=request, user=current_user, table_name="zones",
                       record_id=zone.zone_id, new_value=zone.to_dict())
    await db.commit()

    response = ZoneResponse.model_validate(zone)
    await notification_service.broadcast_zone_update(
        zone.camera_id, zone.zone_id, "created", response.model_dump(mode="json")
    )
    return response


@router.put("/{zone_id}", response_model=ZoneResponse, summary="Update a zone")
async def update_zone(
    request: Request,
    zone_id: int = Path(..., ge=1),
    payload: ZoneUpdate = ...,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> ZoneResponse:
    zone = await zone_crud.get(db, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Zone {zone_id} not found")
    await ensure_camera_access(request, db, current_user, zone.camera_id)

    before = zone.to_dict()
    updates = payload.model_dump(exclude_unset=True)
    updates.pop("risk_bonus", None)  # policy value, recomputed from the zone type below
    if payload.geo_polygon is not None:
        updates["polygon"] = await project_from_map(db, zone.camera_id, payload.geo_polygon)
    elif payload.polygon is not None:
        # A polygon edited on the camera image detaches the zone from the map drawing it came from.
        updates["polygon"] = validate_integer_polygon(payload.polygon)
        updates["geo_polygon"] = None
    if payload.zone_type is not None:
        updates["risk_bonus"] = risk_bonus_for(payload.zone_type)

    # exclude_none=False so a polygon edited on the image can clear geo_polygon (detach from the map).
    zone = await zone_crud.update(db, zone, updates, exclude_none=False)
    await audit_action(db, "UPDATE_ZONE", request=request, user=current_user, table_name="zones",
                       record_id=zone_id, old_value=before, new_value=zone.to_dict())
    await db.commit()

    response = ZoneResponse.model_validate(zone)
    await notification_service.broadcast_zone_update(
        zone.camera_id, zone.zone_id, "updated", response.model_dump(mode="json")
    )
    return response


@router.delete("/{zone_id}", response_model=MessageResponse, summary="Deactivate a zone (soft delete)")
async def delete_zone(
    request: Request,
    zone_id: int = Path(..., ge=1),
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    zone = await zone_crud.get(db, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Zone {zone_id} not found")
    await ensure_camera_access(request, db, current_user, zone.camera_id)

    before = zone.to_dict()
    camera_id = zone.camera_id
    await zone_crud.update(db, zone, {"is_active": False})
    await audit_action(db, "DELETE_ZONE", request=request, user=current_user, table_name="zones",
                       record_id=zone_id, old_value=before, new_value={"is_active": False, "deletion": "soft"})
    await db.commit()

    await notification_service.broadcast_zone_deleted(camera_id, zone_id)
    return MessageResponse(message="Zone deleted")
