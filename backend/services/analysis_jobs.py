"""Video analysis jobs: run the ML detector over an uploaded video and turn its findings into alerts.

The backend never imports torch/TensorRT. Each job runs `python -m ml.analysis_job` in a child process that
writes its progress to a JSON file (atomically replaced). When the worker finishes, the backend validates
the findings and persists high-risk/critical alerts plus their snapshot evidence (re-hashed here and compared
with the worker's SHA-256 before the record is written). One job runs at a time: the GPU is shared with the
live pipeline.
"""
import asyncio
import json
import logging
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from backend.core.enums import EvidenceType
from backend.database.crud import alert_crud, evidence_crud
from backend.database.database import get_db_context
from backend.models import Camera
from backend.schemas.alert import AlertCreate
from backend.services.audit import audit_action, audit_standalone
from backend.services.evidence_service import evidence_service
from backend.services.notification import notification_service

logger = logging.getLogger("sih26187.analysis")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALERT_LEVELS = {"high_risk", "critical"}
POLL_SECONDS = 1.0
JOB_TIMEOUT_SECONDS = 3 * 60 * 60
MAX_KEPT_JOBS = 50


@dataclass
class AnalysisJob:
    job_id: str
    video_path: str
    camera_id: Optional[str]
    evidence_id: Optional[int]
    user_id: Any
    username: str
    recorded_at: datetime
    delete_video_after: bool = False
    status: str = "queued"  # queued | loading | running | complete | failed
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    progress: Dict[str, Any] = field(default_factory=dict)
    summary: Optional[Dict[str, Any]] = None
    alerts_created: List[str] = field(default_factory=list)
    evidence_created: List[int] = field(default_factory=list)
    process: Optional[subprocess.Popen] = None

    @property
    def job_file(self) -> Path:
        return analysis_dir() / f"{self.job_id}.json"

    @property
    def frame_file(self) -> Path:
        """Latest annotated frame written by the worker for the live view."""
        return analysis_dir() / f"{self.job_id}.jpg"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "evidence_id": self.evidence_id,
            "camera_id": self.camera_id,
            "standalone": self.camera_id is None,
            "status": self.status,
            "percent": float(self.progress.get("percent", 100.0 if self.status == "complete" else 0.0)),
            "frames_read": int(self.progress.get("frames_read", 0)),
            "frames_total": int(self.progress.get("frames_total", 0)),
            "persons": int(self.progress.get("persons", 0)),
            "vehicles": int(self.progress.get("vehicles", 0)),
            "animals": int(self.progress.get("animals", 0)),
            "weapons": int(self.progress.get("weapons", 0)),
            "alerts": int(self.progress.get("alerts", 0)),
            "video_seconds": float(self.progress.get("video_seconds", 0.0)),
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "summary": self.summary,
            "alerts_created": list(self.alerts_created),
            "evidence_created": list(self.evidence_created),
        }


def analysis_dir() -> Path:
    return evidence_service.evidence_root / "analysis"


def standalone_upload_dir() -> Path:
    return analysis_dir() / "uploads"


class AnalysisJobManager:
    def __init__(self) -> None:
        self.jobs: Dict[str, AnalysisJob] = {}
        self._lock = asyncio.Lock()

    def active_job(self) -> Optional[AnalysisJob]:
        return next((job for job in self.jobs.values() if job.status in ("queued", "loading", "running")), None)

    def get(self, job_id: str) -> Optional[AnalysisJob]:
        return self.jobs.get(job_id)

    def latest_for_evidence(self, evidence_id: int) -> Optional[AnalysisJob]:
        matches = [job for job in self.jobs.values() if job.evidence_id == evidence_id]
        return max(matches, key=lambda job: job.created_at) if matches else None

    async def start(
        self,
        *,
        video_path: str,
        camera_id: Optional[str],
        evidence_id: Optional[int],
        user: Any,
        recorded_at: Optional[datetime] = None,
        delete_video_after: bool = False,
        frame_step: int = 5,
    ) -> AnalysisJob:
        async with self._lock:
            if self.active_job() is not None:
                raise RuntimeError("Another video analysis is already running; wait for it to finish")
            analysis_dir().mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(cleanup_stale_files)
            job = AnalysisJob(
                job_id=uuid.uuid4().hex,
                video_path=video_path,
                camera_id=camera_id,
                evidence_id=evidence_id,
                user_id=getattr(user, "user_id", None),
                username=getattr(user, "username", "unknown"),
                recorded_at=(recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc),
                delete_video_after=delete_video_after,
            )
            command = [
                sys.executable, "-m", "ml.analysis_job",
                "--video", video_path,
                "--job-file", str(job.job_file),
                "--frame-step", str(max(1, frame_step)),
                "--recorded-at", job.recorded_at.isoformat(),
            ]
            command += ["--camera", camera_id] if camera_id else ["--standalone"]
            log_file = open(analysis_dir() / f"{job.job_id}.log", "wb")  # noqa: SIM115 - closed in _watch
            creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            job.process = subprocess.Popen(
                command, cwd=str(PROJECT_ROOT), stdout=log_file, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, creationflags=creation,
            )
            self.jobs[job.job_id] = job
            self._prune()
            asyncio.get_running_loop().create_task(self._watch(job, log_file), name=f"analysis-{job.job_id}")
            logger.info("Video analysis %s started (pid=%s, camera=%s, evidence=%s)",
                        job.job_id, job.process.pid, camera_id or "standalone", evidence_id)
            return job

    def _prune(self) -> None:
        finished = sorted((job for job in self.jobs.values() if job.finished_at), key=lambda job: job.finished_at)
        for job in finished[: max(0, len(self.jobs) - MAX_KEPT_JOBS)]:
            self.jobs.pop(job.job_id, None)

    def _read_progress(self, job: AnalysisJob) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(job.job_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    async def _watch(self, job: AnalysisJob, log_file) -> None:
        started = asyncio.get_running_loop().time()
        try:
            while True:
                data = await asyncio.to_thread(self._read_progress, job)
                if data:
                    job.progress = data.get("progress", job.progress)
                    if data.get("status") in ("loading", "running"):
                        job.status = data["status"]
                code = job.process.poll() if job.process else 0
                if code is not None:
                    break
                if asyncio.get_running_loop().time() - started > JOB_TIMEOUT_SECONDS:
                    job.process.kill()
                    job.error = "Analysis timed out"
                    break
                await asyncio.sleep(POLL_SECONDS)

            data = await asyncio.to_thread(self._read_progress, job)
            if job.error is None and data and data.get("status") == "complete" and data.get("result"):
                job.progress = data.get("progress", job.progress)
                await self._finalise(job, data["result"])
            else:
                job.status = "failed"
                job.error = job.error or (data or {}).get("error") or f"Analysis worker exited with code {code}"
        except Exception as exc:  # the watcher must always settle the job
            logger.exception("Video analysis %s failed", job.job_id)
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.finished_at = datetime.now(timezone.utc)
            log_file.close()
            if job.delete_video_after:
                await asyncio.to_thread(Path(job.video_path).unlink, True)
            if job.camera_id is None:
                # Standalone analyses store nothing; the live-view frame is removed once viewers had time to read it.
                asyncio.get_running_loop().call_later(30, lambda: job.frame_file.unlink(missing_ok=True))
            await audit_standalone(
                "VIDEO_ANALYSIS_COMPLETE" if job.status == "complete" else "VIDEO_ANALYSIS_FAILED",
                user=SimpleNamespace(user_id=job.user_id, username=job.username),
                table_name="evidence", record_id=job.evidence_id,
                new_value={"job_id": job.job_id, "camera_id": job.camera_id, "error": job.error,
                           "alerts_created": len(job.alerts_created), "evidence_created": len(job.evidence_created),
                           "persons": job.progress.get("persons"), "vehicles": job.progress.get("vehicles")},
                status="success" if job.status == "complete" else "error",
            )

    async def _finalise(self, job: AnalysisJob, result: Dict[str, Any]) -> None:
        if job.camera_id:
            await self._persist_alerts(job, result)
        timeline = result.get("timeline") or []
        job.summary = {
            "persons_found": len(result.get("persons_found") or []),
            "vehicles_found": len(result.get("vehicles_found") or []),
            "animals_found": len(result.get("animals_found") or []),
            "weapons_found": {kind: len(carriers) for kind, carriers in (result.get("weapons_found") or {}).items()},
            "weapon_sightings": dict(result.get("weapon_sightings") or {}),
            "alerts_detected": len(result.get("alerts_fired") or []),
            "alerts_created": len(job.alerts_created),
            "evidence_saved": len(job.evidence_created),
            "high_risk_moments": high_risk_moments(timeline),
            "duration_seconds": result.get("duration_seconds"),
            "frames_read": result.get("frames_read"),
            "frames_processed": result.get("frames_processed"),
            "fps": result.get("fps"),
            "resolution": result.get("resolution"),
            "zones_used": result.get("zones_used"),
        }
        job.status = "complete"

    async def _persist_alerts(self, job: AnalysisJob, result: Dict[str, Any]) -> None:
        digests = {entry.get("alert_id"): entry.get("sha256") for entry in result.get("evidence_saved") or []}
        critical: List[Dict[str, Any]] = []
        async with get_db_context() as db:
            camera = await db.get(Camera, job.camera_id)
            if camera is None:
                raise ValueError(f"Camera {job.camera_id} no longer exists")
            actor = SimpleNamespace(user_id=job.user_id, username=job.username)
            for fired in result.get("alerts_fired") or []:
                if fired.get("risk_level") not in ALERT_LEVELS:
                    continue
                alert_id = fired.get("alert_id")
                if not alert_id or await alert_crud.get(db, alert_id) is not None:
                    continue
                stamp = job.recorded_at + timedelta(seconds=float(fired.get("video_seconds") or 0))
                try:
                    validated = AlertCreate(
                        alert_id=alert_id,
                        camera_id=camera.camera_id,
                        track_id=fired.get("track_id"),
                        alert_type=fired.get("alert_type"),
                        risk_score=fired.get("risk_score"),
                        risk_level=fired.get("risk_level"),
                        risk_reasons=[*(fired.get("risk_reasons") or []), f"video_analysis@{fired.get('video_seconds')}s"],
                        timestamp=stamp,
                    )
                except ValidationError as exc:
                    logger.warning("Analysis %s: alert %s rejected: %s", job.job_id, alert_id, exc.errors(include_url=False)[0]["msg"])
                    continue
                data = validated.model_dump()
                data.update({"gps_lat": camera.gps_lat, "gps_lng": camera.gps_lng, "timestamp": stamp})
                alert = await alert_crud.create(db, data)

                snapshot = fired.get("snapshot_path")
                if snapshot and evidence_service.is_within_evidence_root(snapshot) and Path(snapshot).is_file():
                    file_hash = await asyncio.to_thread(evidence_service.compute_sha256, snapshot)
                    if digests.get(alert_id) and digests[alert_id] != file_hash:
                        logger.error("Analysis %s: snapshot hash mismatch for %s; not recorded as evidence", job.job_id, alert_id)
                    else:
                        resolved = Path(snapshot).resolve().as_posix()
                        alert.snapshot_path = resolved
                        record = await evidence_crud.create(db, {
                            "alert_id": alert.alert_id,
                            "camera_id": camera.camera_id,
                            "track_id": fired.get("track_id"),
                            "evidence_type": EvidenceType.SNAPSHOT,
                            "file_path": resolved,
                            "file_name": Path(resolved).name,
                            "file_size_bytes": await asyncio.to_thread(evidence_service.get_file_size, resolved),
                            "file_hash": file_hash,
                            "gps_lat": camera.gps_lat,
                            "gps_lng": camera.gps_lng,
                        })
                        job.evidence_created.append(record.evidence_id)
                await audit_action(
                    db, "ALERT_CREATED", user=actor, table_name="alerts", record_id=alert.alert_id,
                    new_value={"source": "video_analysis", "job_id": job.job_id, "source_evidence_id": job.evidence_id,
                               "alert_type": alert.alert_type.value, "risk_score": alert.risk_score,
                               "video_seconds": fired.get("video_seconds")},
                )
                job.alerts_created.append(alert.alert_id)
                if alert.risk_level.value == "critical":
                    critical.append({"alert_id": alert.alert_id, "risk_score": alert.risk_score,
                                     "risk_reasons": list(alert.risk_reasons or []), "alert_type": alert.alert_type.value})
            await db.commit()
        for item in critical:
            await notification_service.broadcast_critical_alert(
                item["alert_id"], job.camera_id, item["risk_score"], item["risk_reasons"], item["alert_type"]
            )

    async def shutdown(self) -> None:
        for job in self.jobs.values():
            if job.process is not None and job.process.poll() is None:
                job.process.kill()


def high_risk_moments(timeline: List[Dict[str, Any]], threshold: int = 60, gap_seconds: float = 2.0) -> List[Dict[str, Any]]:
    """Merge consecutive timeline samples above `threshold` into [start, end] moments with their peak risk."""
    moments: List[Dict[str, Any]] = []
    for sample in timeline:
        risk = int(sample.get("max_risk_score") or 0)
        second = float(sample.get("video_seconds") or 0)
        if risk <= threshold:
            continue
        if moments and second - moments[-1]["end_seconds"] <= gap_seconds:
            moments[-1]["end_seconds"] = second
            moments[-1]["peak_risk"] = max(moments[-1]["peak_risk"], risk)
        else:
            moments.append({"start_seconds": second, "end_seconds": second, "peak_risk": risk})
    return moments[:50]


analysis_jobs = AnalysisJobManager()


def cleanup_stale_files(max_age_hours: int = 72) -> None:
    """Remove worker progress/log files and standalone uploads older than `max_age_hours`."""
    cutoff = datetime.now().timestamp() - max_age_hours * 3600
    for directory in (analysis_dir(), standalone_upload_dir()):
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue

