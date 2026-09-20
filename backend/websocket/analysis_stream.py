"""Live view of a video analysis job.

    ws://host/ws/analysis/{job_id}?token=<JWT>

Pushes, roughly four times per second while the worker runs:
  {"type": "analysis_progress", "job": {...}}            status, percent, live counts
  {"type": "analysis_frame", "frame": "<base64 JPEG>"}    latest annotated frame (only when it changed)
and finally {"type": "analysis_complete", "job": {...}} with the summary, after which the socket closes.
WebSocket traffic is not counted against the per-client HTTP rate limit, so the live view never competes
with the rest of the console for request budget.
"""
import asyncio
import base64
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

from backend.core.auth import resolve_user_from_token, user_can_access_camera
from backend.core.enums import UserRole
from backend.database.database import get_db_context
from backend.models import Camera
from backend.schemas.api import AnalysisJobResponse
from backend.services.analysis_jobs import AnalysisJob, analysis_jobs
from backend.services.audit import audit_standalone

logger = logging.getLogger("sih26187.analysis_stream")

router = APIRouter(tags=["WebSocket"])

PUSH_INTERVAL_SECONDS = 0.25
CLOSE_POLICY_VIOLATION = 1008
TERMINAL = ("complete", "failed")


def _job_payload(job: AnalysisJob) -> dict:
    return AnalysisJobResponse.model_validate(job.to_dict()).model_dump(mode="json")


def _read_frame(job: AnalysisJob, last_mtime: float) -> tuple:
    """(base64 JPEG or None, mtime) of the worker's latest annotated frame."""
    path = job.frame_file
    try:
        mtime = path.stat().st_mtime
        if mtime == last_mtime:
            return None, last_mtime
        return base64.b64encode(path.read_bytes()).decode("ascii"), mtime
    except OSError:
        return None, last_mtime


async def _authorise(websocket: WebSocket, job_id: str, token: Optional[str]):
    if not token:
        return None, None, "Missing token"
    async with get_db_context() as db:
        try:
            user, _claims = await resolve_user_from_token(db, token)
        except HTTPException as exc:
            return None, None, str(exc.detail)
        job = analysis_jobs.get(job_id)
        if job is None:
            return user, None, "Analysis job not found"
        if job.camera_id:
            camera = await db.get(Camera, job.camera_id)
            if camera is None or not user_can_access_camera(user, camera.camera_id, camera.zone_region):
                return user, None, "No access to this camera"
        elif str(job.user_id) != str(user.user_id) and user.role != UserRole.ADMIN:
            return user, None, "Analysis job not found"
        db.expunge(user)
    return user, job, None


@router.websocket("/analysis/{job_id}")
async def analysis_stream(websocket: WebSocket, job_id: str, token: Optional[str] = Query(None)) -> None:
    user, job, error = await _authorise(websocket, job_id, token)
    if error:
        await audit_standalone("WS_ANALYSIS_DENIED", user=user, table_name="evidence", record_id=job_id,
                               new_value={"reason": error}, status="denied",
                               ip_address=websocket.client.host if websocket.client else None)
        await websocket.close(code=CLOSE_POLICY_VIOLATION, reason=error[:120])
        return

    await websocket.accept()
    await audit_standalone("WS_ANALYSIS_VIEW", user=user, table_name="evidence", record_id=job.evidence_id,
                           new_value={"job_id": job.job_id},
                           ip_address=websocket.client.host if websocket.client else None)
    last_mtime = 0.0
    last_progress = None
    try:
        while True:
            # Read the worker's progress directly for a smooth live view (the manager polls once a second).
            data = await asyncio.to_thread(analysis_jobs._read_progress, job)
            if data and job.status not in TERMINAL:
                job.progress = data.get("progress", job.progress)
                if data.get("status") in ("loading", "running"):
                    job.status = data["status"]
            frame, last_mtime = await asyncio.to_thread(_read_frame, job, last_mtime)
            if frame is not None:
                await websocket.send_json({"type": "analysis_frame", "frame": frame})
            payload = _job_payload(job)
            if payload != last_progress:
                last_progress = payload
                await websocket.send_json({"type": "analysis_progress", "job": payload})
            if job.status in TERMINAL and job.finished_at is not None:
                await websocket.send_json({"type": "analysis_complete", "job": payload})
                await websocket.close(code=1000)
                return
            await asyncio.sleep(PUSH_INTERVAL_SECONDS)
    except WebSocketDisconnect:
        return
    except Exception:
        logger.exception("Analysis stream %s failed", job_id)
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
