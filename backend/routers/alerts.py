"""Alert listing, detail, acknowledgement, statistics and deletion."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import normalize_camera_id, restrict_to_visible_cameras, visible_camera_ids_subquery
from backend.core.api_config import api_settings
from backend.core.auth import ensure_camera_access
from backend.core.dependencies import get_admin_user, get_current_active_user, get_operator_or_above
from backend.core.enums import AlertType, RiskLevel, UserRole
from backend.database.crud import alert_crud
from backend.database.database import get_db
from backend.models import Alert, Evidence
from backend.models.user import User
from backend.schemas.alert import AlertAcknowledge, AlertFilter, AlertListResponse, AlertResponse
from backend.schemas.api import AlertStatsResponse, CameraAlertCount, HourlyCount
from backend.services.audit import audit_action
from backend.services.notification import notification_service
from backend.services.system_metrics import start_of_reporting_day

router = APIRouter(tags=["Alerts"])

TOP_CAMERAS_IN_STATS = 10


def _local_hour(column):
    """Hour of day in the reporting timezone (IST): timestamptz AT TIME ZONE INTERVAL '330 minutes'."""
    offset = api_settings.REPORTING_UTC_OFFSET_MINUTES
    return func.extract("hour", func.timezone(text(f"INTERVAL '{offset} minutes'"), column))


@router.get("/stats", response_model=AlertStatsResponse, summary="Alert statistics for today")
async def alert_stats(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AlertStatsResponse:
    scope = visible_camera_ids_subquery(current_user)
    day_start = start_of_reporting_day()

    def scoped(query):
        return query if scope is None else query.where(Alert.camera_id.in_(scope))

    today = scoped(select(func.count()).select_from(Alert).where(Alert.timestamp >= day_start))
    total_today = int((await db.execute(today)).scalar() or 0)
    critical_today = int((await db.execute(today.where(Alert.risk_level == RiskLevel.CRITICAL))).scalar() or 0)
    unacknowledged = int((await db.execute(
        scoped(select(func.count()).select_from(Alert).where(Alert.acknowledged.is_(False)))
    )).scalar() or 0)

    type_rows = (await db.execute(
        scoped(select(Alert.alert_type, func.count()).where(Alert.timestamp >= day_start)).group_by(Alert.alert_type)
    )).all()
    level_rows = (await db.execute(
        scoped(select(Alert.risk_level, func.count()).where(Alert.timestamp >= day_start)).group_by(Alert.risk_level)
    )).all()
    camera_rows = (await db.execute(
        scoped(select(Alert.camera_id, func.count().label("count")).where(Alert.timestamp >= day_start))
        .group_by(Alert.camera_id).order_by(func.count().desc(), Alert.camera_id).limit(TOP_CAMERAS_IN_STATS)
    )).all()
    hour_rows = (await db.execute(
        scoped(select(_local_hour(Alert.timestamp).label("hour"), func.count()).where(Alert.timestamp >= day_start))
        .group_by(text("hour")).order_by(text("hour"))
    )).all()

    by_type = {alert_type.value: 0 for alert_type in AlertType}
    by_type.update({getattr(t, "value", t): int(c) for t, c in type_rows})
    by_risk_level = {level.value: 0 for level in RiskLevel}
    by_risk_level.update({getattr(level, "value", level): int(c) for level, c in level_rows})
    hourly = {int(hour): int(count) for hour, count in hour_rows}

    await audit_action(db, "VIEW_ALERT_STATS", request=request, user=current_user, table_name="alerts",
                       new_value={"total_today": total_today, "critical_today": critical_today})
    await db.commit()
    return AlertStatsResponse(
        total_today=total_today,
        critical_today=critical_today,
        unacknowledged=unacknowledged,
        by_type=by_type,
        by_risk_level=by_risk_level,
        by_camera=[CameraAlertCount(camera_id=cid, count=int(count)) for cid, count in camera_rows],
        hourly_trend=[HourlyCount(hour=hour, count=hourly.get(hour, 0)) for hour in range(24)],
        timezone=f"UTC+{api_settings.REPORTING_UTC_OFFSET_MINUTES // 60:02d}:{api_settings.REPORTING_UTC_OFFSET_MINUTES % 60:02d}",
    )


class ClearTestDataResponse(BaseModel):
    cleared: int
    before: datetime
    message: str


TEST_DATA_NOTE = "Cleared as test data (raised before the current reporting day)"


@router.post("/clear-test-data", response_model=ClearTestDataResponse,
             summary="Close all unacknowledged alerts raised before today (admin only, non-destructive)")
async def clear_test_data(
    request: Request,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> ClearTestDataResponse:
    """Old demo/test alerts are acknowledged as false alarms instead of deleted: evidence rows reference
    alerts (ON DELETE RESTRICT) and deleting them would break the SHA-256 chain of custody."""
    day_start = start_of_reporting_day()
    rows = (await db.execute(
        select(Alert).where(Alert.acknowledged.is_(False), Alert.timestamp < day_start)
    )).scalars().all()
    for alert in rows:
        alert.acknowledge(current_user.user_id, True, TEST_DATA_NOTE)
    await db.flush()
    await audit_action(db, "CLEAR_TEST_ALERTS", request=request, user=current_user, table_name="alerts",
                       new_value={"cleared": len(rows), "before": day_start.isoformat(),
                                  "alert_ids": [alert.alert_id for alert in rows[:500]]})
    await db.commit()
    return ClearTestDataResponse(
        cleared=len(rows),
        before=day_start,
        message=f"{len(rows)} alert(s) raised before {day_start.date().isoformat()} closed as test data",
    )


@router.get("", response_model=AlertListResponse, summary="List alerts (unacknowledged first, newest first)")
async def list_alerts(
    request: Request,
    camera_id: Optional[str] = Query(None, max_length=20),
    alert_type: Optional[AlertType] = Query(None),
    risk_level: Optional[RiskLevel] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    acknowledged: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AlertListResponse:
    try:
        filters = AlertFilter(
            camera_id=normalize_camera_id(camera_id), alert_type=alert_type, risk_level=risk_level,
            date_from=date_from, date_to=date_to, acknowledged=acknowledged, skip=skip, limit=limit,
        )
    except ValidationError as exc:
        # errors() can carry raw datetime inputs, which are not JSON serialisable.
        detail = [
            {"loc": [str(part) for part in error["loc"]], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors(include_url=False)
        ]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)

    # Everything except the acknowledged filter; unacknowledged_count always counts open alerts
    # matching the other filters, so the badge stays meaningful when browsing acknowledged=true.
    conditions = []
    if filters.camera_id:
        conditions.append(Alert.camera_id == filters.camera_id)
    if filters.alert_type:
        conditions.append(Alert.alert_type == filters.alert_type)
    if filters.risk_level:
        conditions.append(Alert.risk_level == filters.risk_level)
    if filters.date_from:
        conditions.append(Alert.timestamp >= filters.date_from)
    if filters.date_to:
        conditions.append(Alert.timestamp <= filters.date_to)

    query = select(Alert)
    count_query = select(func.count()).select_from(Alert)
    unack_query = select(func.count()).select_from(Alert).where(Alert.acknowledged.is_(False))
    for condition in conditions:
        query = query.where(condition)
        count_query = count_query.where(condition)
        unack_query = unack_query.where(condition)
    if filters.acknowledged is not None:
        query = query.where(Alert.acknowledged.is_(filters.acknowledged))
        count_query = count_query.where(Alert.acknowledged.is_(filters.acknowledged))

    query = restrict_to_visible_cameras(query, Alert.camera_id, current_user)
    count_query = restrict_to_visible_cameras(count_query, Alert.camera_id, current_user)
    unack_query = restrict_to_visible_cameras(unack_query, Alert.camera_id, current_user)

    rows = (await db.execute(
        query.order_by(Alert.acknowledged.asc(), Alert.timestamp.desc(), Alert.alert_id)
        .offset(filters.skip).limit(filters.limit)
    )).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)
    unacknowledged = int((await db.execute(unack_query)).scalar() or 0)

    await audit_action(db, "LIST_ALERTS", request=request, user=current_user, table_name="alerts",
                       new_value={"filters": filters.model_dump(mode="json", exclude_none=True),
                                  "returned": len(rows), "total": total})
    await db.commit()
    return AlertListResponse(
        items=[AlertResponse.model_validate(alert) for alert in rows],
        total=total,
        unacknowledged_count=unacknowledged,
    )


@router.get("/{alert_id}", response_model=AlertResponse, summary="Alert detail")
async def get_alert(
    request: Request,
    alert_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AlertResponse:
    alert = await alert_crud.get(db, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")
    await ensure_camera_access(request, db, current_user, alert.camera_id)

    await audit_action(db, "VIEW_ALERT", request=request, user=current_user, table_name="alerts", record_id=alert_id)
    await db.commit()
    return AlertResponse.model_validate(alert)


@router.patch("/{alert_id}/acknowledge", response_model=AlertResponse, summary="Acknowledge an alert")
async def acknowledge_alert(
    request: Request,
    alert_id: str,
    payload: AlertAcknowledge,
    current_user: User = Depends(get_operator_or_above),
    db: AsyncSession = Depends(get_db),
) -> AlertResponse:
    alert = await alert_crud.get(db, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")
    await ensure_camera_access(request, db, current_user, alert.camera_id)

    # Only an admin may record an acknowledgement on behalf of somebody else.
    if payload.acknowledged_by != current_user.user_id and current_user.role != UserRole.ADMIN:
        await audit_action(db, "ACKNOWLEDGE_ALERT", request=request, user=current_user, table_name="alerts",
                           record_id=alert_id, new_value={"reason": "acknowledged_by_mismatch"},
                           status="denied", commit=True)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "acknowledged_by must be your own user_id")
    if alert.acknowledged:
        await audit_action(db, "ACKNOWLEDGE_ALERT", request=request, user=current_user, table_name="alerts",
                           record_id=alert_id, new_value={"reason": "already_acknowledged"},
                           status="failure", commit=True)
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert {alert_id} is already acknowledged")

    before = alert.to_dict()
    alert.acknowledge(payload.acknowledged_by, payload.false_alarm, payload.notes)
    await db.flush()
    await audit_action(db, "ACKNOWLEDGE_ALERT", request=request, user=current_user, table_name="alerts",
                       record_id=alert_id, old_value=before, new_value=alert.to_dict())
    await db.commit()

    await notification_service.broadcast_alert_acknowledged(
        alert_id=alert.alert_id,
        acknowledged_by=str(payload.acknowledged_by),
        camera_id=alert.camera_id,
        false_alarm=alert.false_alarm,
        acknowledged_by_username=current_user.username,
    )
    return AlertResponse.model_validate(alert)


@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an alert (admin only)")
async def delete_alert(
    request: Request,
    alert_id: str,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    alert = await alert_crud.get(db, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")

    evidence_count = int((await db.execute(
        select(func.count()).select_from(Evidence).where(Evidence.alert_id == alert_id)
    )).scalar() or 0)
    if evidence_count:
        await audit_action(db, "DELETE_ALERT", request=request, user=current_user, table_name="alerts",
                           record_id=alert_id, new_value={"reason": "evidence_exists", "evidence_count": evidence_count},
                           status="failure", commit=True)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Cannot delete alert with {evidence_count} evidence record(s) attached")

    before = alert.to_dict()
    await db.delete(alert)
    await db.flush()
    await audit_action(db, "DELETE_ALERT", request=request, user=current_user, table_name="alerts",
                       record_id=alert_id, old_value=before)
    await db.commit()
