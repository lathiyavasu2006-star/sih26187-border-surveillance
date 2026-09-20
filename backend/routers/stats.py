"""Live system statistics, health probe, audit log browser and system health history."""
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import visible_camera_ids_subquery
from backend.core.dependencies import get_admin_or_regional_head, get_admin_user, get_current_active_user
from backend.core.enums import AuditStatus
from backend.core.runtime import runtime_state
from backend.core.config import settings
from backend.database.database import check_db_connection, get_db
from backend.models import AuditLog, SystemHealth
from backend.models.user import User
from backend.schemas.api import HealthResponse, StatsResponse
from backend.schemas.audit_log import AuditLogListResponse, AuditLogResponse
from backend.schemas.system_health import SystemHealthListResponse, SystemHealthResponse
from backend.services.audit import audit_action
from backend.services.system_metrics import collect_host_metrics_async, gather_operational_counts
from backend.websocket.manager import manager

router = APIRouter(tags=["Stats"])
public_router = APIRouter(tags=["Stats"])

MAX_HISTORY_HOURS = 720


@router.get("", response_model=StatsResponse, summary="Live system statistics")
async def get_stats(
    request: Request,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> StatsResponse:
    metrics = await collect_host_metrics_async()
    counts = await gather_operational_counts(db, camera_scope=visible_camera_ids_subquery(current_user))

    await audit_action(db, "VIEW_STATS", request=request, user=current_user, table_name="system_health")
    await db.commit()
    return StatsResponse(
        persons_detected_today=counts.persons_today,
        vehicles_detected_today=counts.vehicles_today,
        active_alerts=counts.active_alerts,
        critical_alerts=counts.critical_alerts,
        cameras_online=counts.cameras_online,
        cameras_total=counts.cameras_total,
        cameras_offline=counts.cameras_offline,
        cameras_degraded=counts.cameras_degraded,
        avg_system_fps=runtime_state.average_fps(settings.CAMERA_OFFLINE_THRESHOLD_SECONDS),
        cpu_percent=metrics.cpu_percent,
        ram_percent=metrics.ram_percent,
        gpu_percent=metrics.gpu.gpu_percent,
        gpu_memory_used_mb=metrics.gpu.memory_used_mb,
        gpu_memory_total_mb=metrics.gpu.memory_total_mb,
        disk_free_gb=metrics.disk_free_gb,
        uptime_seconds=runtime_state.uptime_seconds(),
        websocket_clients=manager.get_connection_count(),
        timestamp=datetime.now(timezone.utc),
    )


@public_router.get("/health", response_model=HealthResponse, summary="Health probe (no authentication)")
async def health(request: Request) -> HealthResponse:
    from backend.services.camera_monitor import monitor_status

    db_ok = await check_db_connection()
    monitor = monitor_status(getattr(request.app.state, "camera_monitor_task", None))
    if not db_ok:
        overall = "down"
    elif monitor == "stopped":
        overall = "degraded"
    else:
        overall = "healthy"
    return HealthResponse(
        status=overall,
        db="connected" if db_ok else "disconnected",
        camera_monitor=monitor,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/audit-log", response_model=AuditLogListResponse, summary="Browse the audit trail (admin only)")
async def audit_log(
    request: Request,
    user_id: Optional[UUID] = Query(None),
    action: Optional[str] = Query(None, max_length=100),
    status_filter: Optional[AuditStatus] = Query(None, alias="status"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AuditLogListResponse:
    conditions = []
    if user_id:
        conditions.append(AuditLog.user_id == user_id)
    if action:
        conditions.append(func.upper(AuditLog.action) == action.upper())
    if status_filter:
        conditions.append(AuditLog.status == status_filter.value)
    if date_from:
        conditions.append(AuditLog.timestamp >= date_from)
    if date_to:
        conditions.append(AuditLog.timestamp <= date_to)

    query = select(AuditLog)
    count_query = select(func.count()).select_from(AuditLog)
    for condition in conditions:
        query, count_query = query.where(condition), count_query.where(condition)

    rows = (await db.execute(
        query.order_by(AuditLog.timestamp.desc(), AuditLog.log_id.desc()).offset(skip).limit(limit)
    )).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)

    await audit_action(db, "VIEW_AUDIT_LOG", request=request, user=current_user, table_name="audit_logs",
                       new_value={"action": action, "returned": len(rows), "total": total})
    await db.commit()
    return AuditLogListResponse(items=[AuditLogResponse.model_validate(row) for row in rows], total=total)


@router.get("/system-health-history", response_model=SystemHealthListResponse,
            summary="System health history (admin or regional head)")
async def system_health_history(
    request: Request,
    server_name: Optional[str] = Query(None, max_length=50),
    hours: int = Query(24, ge=1, le=MAX_HISTORY_HOURS),
    current_user: User = Depends(get_admin_or_regional_head),
    db: AsyncSession = Depends(get_db),
) -> SystemHealthListResponse:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(SystemHealth).where(SystemHealth.timestamp >= since)
    count_query = select(func.count()).select_from(SystemHealth).where(SystemHealth.timestamp >= since)
    if server_name:
        query = query.where(SystemHealth.server_name == server_name)
        count_query = count_query.where(SystemHealth.server_name == server_name)

    rows = (await db.execute(query.order_by(SystemHealth.timestamp))).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)

    await audit_action(db, "VIEW_SYSTEM_HEALTH_HISTORY", request=request, user=current_user, table_name="system_health",
                       new_value={"hours": hours, "server_name": server_name, "returned": len(rows)})
    await db.commit()
    return SystemHealthListResponse(items=[SystemHealthResponse.model_validate(row) for row in rows], total=total)
