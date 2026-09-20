"""Track/event history: listing, per-track history, movement timeline and live tracks."""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import normalize_camera_id, restrict_to_visible_cameras
from backend.core.auth import ensure_camera_access
from backend.core.dependencies import get_current_active_user
from backend.database.database import get_db
from backend.models import Event, TrackedObject
from backend.models.user import User
from backend.schemas.api import EventPage, TimelinePoint, TimelineResponse, TrackHistoryResponse
from backend.schemas.event import EventResponse
from backend.services.audit import audit_action

router = APIRouter(tags=["Events"])

TIMELINE_MAX_POINTS = 5000


@router.get("/active", response_model=EventPage, summary="Tracks currently active on camera")
async def active_events(
    request: Request,
    camera_id: Optional[str] = Query(None, max_length=20),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> EventPage:
    query = select(Event).where(Event.is_active.is_(True))
    count_query = select(func.count()).select_from(Event).where(Event.is_active.is_(True))
    camera_id = normalize_camera_id(camera_id)
    if camera_id:
        query, count_query = query.where(Event.camera_id == camera_id), count_query.where(Event.camera_id == camera_id)
    query = restrict_to_visible_cameras(query, Event.camera_id, current_user)
    count_query = restrict_to_visible_cameras(count_query, Event.camera_id, current_user)

    rows = (await db.execute(query.order_by(Event.last_seen.desc()).offset(skip).limit(limit))).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)

    await audit_action(db, "VIEW_ACTIVE_EVENTS", request=request, user=current_user, table_name="events",
                       new_value={"camera_id": camera_id, "returned": len(rows), "total": total})
    await db.commit()
    return EventPage(items=[EventResponse.model_validate(event) for event in rows], total=total)


@router.get("", response_model=EventPage, summary="List events")
async def list_events(
    request: Request,
    camera_id: Optional[str] = Query(None, max_length=20),
    object_class: Optional[str] = Query(None, max_length=20),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    is_active: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> EventPage:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "date_from must be earlier than or equal to date_to")
    for value, name in ((date_from, "date_from"), (date_to, "date_to")):
        if value is not None and value.tzinfo is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} must include a timezone offset")

    conditions = []
    camera_id = normalize_camera_id(camera_id)
    if camera_id:
        conditions.append(Event.camera_id == camera_id)
    if object_class:
        conditions.append(func.lower(Event.object_class) == object_class.lower())
    if is_active is not None:
        conditions.append(Event.is_active.is_(is_active))
    if date_from:
        conditions.append(Event.first_seen >= date_from)
    if date_to:
        conditions.append(Event.first_seen <= date_to)

    query = select(Event)
    count_query = select(func.count()).select_from(Event)
    for condition in conditions:
        query, count_query = query.where(condition), count_query.where(condition)
    query = restrict_to_visible_cameras(query, Event.camera_id, current_user)
    count_query = restrict_to_visible_cameras(count_query, Event.camera_id, current_user)

    rows = (await db.execute(
        query.order_by(Event.first_seen.desc(), Event.event_id.desc()).offset(skip).limit(limit)
    )).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)

    await audit_action(db, "LIST_EVENTS", request=request, user=current_user, table_name="events",
                       new_value={"camera_id": camera_id, "object_class": object_class,
                                  "returned": len(rows), "total": total})
    await db.commit()
    return EventPage(items=[EventResponse.model_validate(event) for event in rows], total=total)


async def _events_for_track(db: AsyncSession, track_id: int, camera_id: Optional[str], user: User) -> List[Event]:
    query = select(Event).where(Event.track_id == track_id)
    if camera_id:
        query = query.where(Event.camera_id == camera_id)
    query = restrict_to_visible_cameras(query, Event.camera_id, user)
    return list((await db.execute(query.order_by(Event.first_seen))).scalars().all())


@router.get("/{track_id}", response_model=TrackHistoryResponse, summary="Full history for a track id")
async def get_track_history(
    request: Request,
    track_id: int = Path(..., ge=0),
    camera_id: Optional[str] = Query(None, max_length=20, description="Track ids repeat across cameras"),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> TrackHistoryResponse:
    camera_id = normalize_camera_id(camera_id)
    if camera_id:
        await ensure_camera_access(request, db, current_user, camera_id)
    events = await _events_for_track(db, track_id, camera_id, current_user)
    if not events:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No events found for track_id {track_id}")

    await audit_action(db, "VIEW_EVENT", request=request, user=current_user, table_name="events",
                       record_id=track_id, new_value={"camera_id": camera_id, "events": len(events)})
    await db.commit()
    return TrackHistoryResponse(
        track_id=track_id,
        events=[EventResponse.model_validate(event) for event in events],
        total_events=len(events),
        total_time_seconds=sum(event.total_time_seconds or 0 for event in events),
        alert_count=sum(event.alert_count or 0 for event in events),
        max_risk_score=max((event.max_risk_score or 0) for event in events),
        cameras=sorted({event.camera_id for event in events}),
    )


@router.get("/{track_id}/timeline", response_model=TimelineResponse, summary="Ordered movement timeline for a track")
async def get_track_timeline(
    request: Request,
    track_id: int = Path(..., ge=0),
    camera_id: Optional[str] = Query(None, max_length=20),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> TimelineResponse:
    camera_id = normalize_camera_id(camera_id)
    if camera_id:
        await ensure_camera_access(request, db, current_user, camera_id)

    query = select(TrackedObject).where(TrackedObject.track_id == track_id)
    if camera_id:
        query = query.where(TrackedObject.camera_id == camera_id)
    query = restrict_to_visible_cameras(query, TrackedObject.camera_id, current_user)
    detections = (await db.execute(
        query.order_by(TrackedObject.timestamp, TrackedObject.id).limit(TIMELINE_MAX_POINTS)
    )).scalars().all()

    if detections:
        source = "tracked_objects"
        points = [
            TimelinePoint(
                timestamp=row.timestamp, camera_id=row.camera_id, cx=row.cx, cy=row.cy,
                zone_name=row.zone_name, zone_type=row.zone_type, risk_score=row.risk_score,
                risk_level=row.risk_level, object_class=row.object_class,
            )
            for row in detections
        ]
    else:
        # Fall back to the position trail stored on the event itself.
        events = await _events_for_track(db, track_id, camera_id, current_user)
        if not events:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No timeline found for track_id {track_id}")
        source = "event_positions"
        points = []
        for event in events:
            for position in (event.positions or []):
                try:
                    stamp = datetime.fromisoformat(position["t"])
                except (KeyError, TypeError, ValueError):
                    continue
                points.append(TimelinePoint(
                    timestamp=stamp, camera_id=event.camera_id, cx=position.get("x"), cy=position.get("y"),
                    risk_score=event.max_risk_score or 0, object_class=event.object_class,
                ))
        points.sort(key=lambda point: point.timestamp)
        points = points[:TIMELINE_MAX_POINTS]

    await audit_action(db, "VIEW_EVENT_TIMELINE", request=request, user=current_user, table_name="events",
                       record_id=track_id, new_value={"camera_id": camera_id, "points": len(points), "source": source})
    await db.commit()
    return TimelineResponse(track_id=track_id, source=source, points=points)
