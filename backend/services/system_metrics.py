"""Host metrics (psutil + nvidia-smi), detection counters and the periodic system_health recorder."""
import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from typing import Optional

import psutil
from sqlalchemy import and_, distinct, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.api_config import api_settings
from backend.core.config import settings
from backend.core.enums import CameraStatus, RiskLevel
from backend.core.runtime import runtime_state
from backend.database.database import get_db_context
from backend.models import Alert, Camera, SystemHealth, TrackedObject

logger = logging.getLogger("sih26187.metrics")

BYTES_PER_GB = 1024 ** 3
GPU_CACHE_SECONDS = 5.0
VEHICLE_CLASSES = ("car", "truck", "bus", "motorcycle", "motorbike", "bicycle", "vehicle", "van", "tractor", "jeep")


@dataclass
class GPUStats:
    gpu_percent: Optional[float] = None
    memory_used_mb: Optional[int] = None
    memory_total_mb: Optional[int] = None


@dataclass
class HostMetrics:
    cpu_percent: float
    ram_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disk_used_gb: float
    disk_free_gb: float
    gpu: GPUStats


_gpu_cache: tuple = (0.0, GPUStats())


def query_gpu_stats() -> GPUStats:
    """nvidia-smi query (cached for 5 s). Returns empty stats if no NVIDIA driver is present."""
    global _gpu_cache
    cached_at, cached = _gpu_cache
    if time.monotonic() - cached_at < GPU_CACHE_SECONDS:
        return cached
    executable = shutil.which("nvidia-smi")
    stats = GPUStats()
    if executable:
        try:
            completed = subprocess.run(
                [executable, "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            first_line = completed.stdout.strip().splitlines()[0] if completed.returncode == 0 and completed.stdout.strip() else ""
            parts = [p.strip() for p in first_line.split(",")]
            if len(parts) == 3:
                stats = GPUStats(float(parts[0]), int(float(parts[1])), int(float(parts[2])))
        except (subprocess.SubprocessError, OSError, ValueError, IndexError) as exc:
            logger.debug("nvidia-smi query failed: %s", exc)
    _gpu_cache = (time.monotonic(), stats)
    return stats


def _disk_root() -> str:
    evidence = Path(settings.EVIDENCE_PATH).resolve()
    return evidence.anchor or str(evidence)


def collect_host_metrics() -> HostMetrics:
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage(_disk_root())
    return HostMetrics(
        cpu_percent=round(psutil.cpu_percent(interval=0.2), 2),
        ram_percent=round(memory.percent, 2),
        ram_used_gb=round(memory.used / BYTES_PER_GB, 2),
        ram_total_gb=round(memory.total / BYTES_PER_GB, 2),
        disk_used_gb=round(disk.used / BYTES_PER_GB, 2),
        disk_free_gb=round(disk.free / BYTES_PER_GB, 2),
        gpu=query_gpu_stats(),
    )


async def collect_host_metrics_async() -> HostMetrics:
    return await asyncio.to_thread(collect_host_metrics)


def start_of_reporting_day(now: Optional[datetime] = None) -> datetime:
    tz = api_settings.reporting_tz
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    return datetime.combine(local_now.date(), dtime.min, tzinfo=tz)


@dataclass
class OperationalCounts:
    persons_today: int
    vehicles_today: int
    active_alerts: int
    critical_alerts: int
    cameras_total: int
    cameras_online: int
    cameras_offline: int
    cameras_degraded: int


async def gather_operational_counts(db: AsyncSession, camera_scope=None) -> OperationalCounts:
    """camera_scope: optional SELECT camera_id subquery restricting the counts (non-admin users)."""
    day_start = start_of_reporting_day()

    def scoped(query, column):
        return query.where(column.in_(camera_scope)) if camera_scope is not None else query

    async def distinct_tracks(classes) -> int:
        tracks = scoped(
            select(distinct(tuple_(TrackedObject.camera_id, TrackedObject.track_id))).where(
                and_(TrackedObject.timestamp >= day_start, func.lower(TrackedObject.object_class).in_(classes))
            ),
            TrackedObject.camera_id,
        ).subquery()
        return int((await db.execute(select(func.count()).select_from(tracks))).scalar() or 0)

    persons = await distinct_tracks(("person",))
    vehicles = await distinct_tracks(VEHICLE_CLASSES)

    unacked = scoped(select(func.count()).select_from(Alert).where(Alert.acknowledged.is_(False)), Alert.camera_id)
    active_alerts = int((await db.execute(unacked)).scalar() or 0)
    critical = int((await db.execute(unacked.where(Alert.risk_level == RiskLevel.CRITICAL))).scalar() or 0)

    status_rows = await db.execute(
        scoped(select(Camera.status, func.count()).group_by(Camera.status), Camera.camera_id)
    )
    by_status = {getattr(status, "value", status): count for status, count in status_rows.all()}
    return OperationalCounts(
        persons_today=persons,
        vehicles_today=vehicles,
        active_alerts=active_alerts,
        critical_alerts=critical,
        cameras_total=sum(by_status.values()),
        cameras_online=by_status.get(CameraStatus.ONLINE.value, 0),
        cameras_offline=by_status.get(CameraStatus.OFFLINE.value, 0),
        cameras_degraded=by_status.get(CameraStatus.DEGRADED.value, 0),
    )


async def record_system_health(server_name: str = "local", region: Optional[str] = None) -> SystemHealth:
    metrics = await collect_host_metrics_async()
    async with get_db_context() as db:
        counts = await gather_operational_counts(db)
        row = SystemHealth(
            server_name=server_name,
            region=region,
            cpu_percent=metrics.cpu_percent,
            ram_percent=metrics.ram_percent,
            ram_used_gb=metrics.ram_used_gb,
            ram_total_gb=metrics.ram_total_gb,
            gpu_percent=metrics.gpu.gpu_percent,
            gpu_memory_used_mb=metrics.gpu.memory_used_mb,
            gpu_memory_total_mb=metrics.gpu.memory_total_mb,
            disk_used_gb=metrics.disk_used_gb,
            disk_free_gb=metrics.disk_free_gb,
            avg_fps=runtime_state.average_fps(settings.CAMERA_OFFLINE_THRESHOLD_SECONDS),
            cameras_online=counts.cameras_online,
            cameras_total=counts.cameras_total,
            active_alerts=counts.active_alerts,
            critical_alerts=counts.critical_alerts,
            persons_detected=counts.persons_today,
            vehicles_detected=counts.vehicles_today,
        )
        db.add(row)
        await db.flush()
    return row


async def system_health_recorder_loop(interval_seconds: Optional[int] = None) -> None:
    interval = interval_seconds or api_settings.SYSTEM_HEALTH_RECORD_INTERVAL_SECONDS
    while True:
        try:
            await record_system_health()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("System health recording failed")
        await asyncio.sleep(interval)
