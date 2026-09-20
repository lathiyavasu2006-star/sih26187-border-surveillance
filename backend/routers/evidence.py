"""Evidence upload, listing, integrity verification, archival and deletion (chain of custody)."""
import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.access import normalize_camera_id, restrict_to_visible_cameras
from backend.core.api_config import api_settings
from backend.core.auth import ensure_camera_access
from backend.core.dependencies import get_admin_user, get_current_active_user, get_supervisor_or_above
from backend.core.enums import EvidenceType
from backend.database.crud import evidence_crud
from backend.database.database import get_db
from backend.models import Alert, Evidence
from backend.models.user import User
from fastapi.responses import FileResponse

from backend.core.enums import UserRole
from backend.schemas.api import (
    AnalysisJobResponse,
    EvidenceArchiveResponse,
    EvidenceDetailResponse,
    EvidencePage,
    EvidenceUploadResponse,
    EvidenceVerifyResponse,
)
from backend.schemas.evidence import EvidenceResponse
from backend.services.audit import audit_action
from backend.services.analysis_jobs import analysis_jobs, standalone_upload_dir
from backend.services.evidence_service import evidence_service
from backend.services.video_playback import playable_path_async

logger = logging.getLogger("sih26187.evidence")

router = APIRouter(tags=["Evidence"])

COPY_CHUNK_BYTES = 1024 * 1024


def _detail(evidence: Evidence, exists: bool) -> EvidenceDetailResponse:
    return EvidenceDetailResponse(
        **EvidenceResponse.model_validate(evidence).model_dump(),
        file_url=evidence_service.file_url(evidence.file_path) or "",
        file_exists=exists,
    )


def _probe_duration_seconds(file_path: str) -> Optional[int]:
    """Best-effort video duration for uploaded clips; None when it cannot be determined."""
    try:
        import cv2

        capture = cv2.VideoCapture(file_path)
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
            frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        finally:
            capture.release()
        if fps > 0 and frames > 0:
            return max(0, int(round(frames / fps)))
    except Exception as exc:
        logger.debug("Duration probe failed for %s: %s", file_path, exc)
    return None


@router.post("/upload", response_model=EvidenceUploadResponse, status_code=status.HTTP_201_CREATED,
             summary="Upload evidence (SHA-256 computed while streaming to disk)")
async def upload_evidence(
    request: Request,
    file: UploadFile = File(..., description="MP4/AVI/MOV/MKV or JPG/JPEG/PNG, max 500 MB"),
    camera_id: str = Form(..., max_length=20),
    alert_id: Optional[str] = Form(None, max_length=40),
    track_id: Optional[int] = Form(None, ge=0),
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> EvidenceUploadResponse:
    camera_id = normalize_camera_id(camera_id)
    camera = await ensure_camera_access(request, db, current_user, camera_id)

    extension = evidence_service.extension_of(file.filename)
    kind = evidence_service.classify_extension(extension)
    if kind is None:
        allowed = sorted(api_settings.video_extensions | api_settings.image_extensions)
        await audit_action(db, "UPLOAD_EVIDENCE", request=request, user=current_user, table_name="evidence",
                           new_value={"filename": file.filename, "reason": "unsupported_type"},
                           status="failure", commit=True)
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"Unsupported file type '.{extension}'. Allowed: {allowed}")

    if alert_id:
        alert = await db.get(Alert, alert_id)
        if alert is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"Alert {alert_id} not found")
        if alert.camera_id != camera_id:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Alert {alert_id} belongs to camera {alert.camera_id}")

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > api_settings.upload_max_bytes + COPY_CHUNK_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"Upload exceeds the {api_settings.UPLOAD_MAX_SIZE_MB} MB limit")

    evidence_service.ensure_directories()
    destination = evidence_service.new_upload_path(file.filename)
    digest = hashlib.sha256()
    written = 0
    try:
        async with aiofiles.open(destination, "wb") as target:
            while True:
                chunk = await file.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if written == 0 and not evidence_service.signature_matches(extension, chunk[:16]):
                    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                        f"File content does not match the .{extension} format")
                written += len(chunk)
                if written > api_settings.upload_max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                        f"Upload exceeds the {api_settings.UPLOAD_MAX_SIZE_MB} MB limit")
                digest.update(chunk)          # hashed as it is written, never re-read from disk
                await target.write(chunk)
        if written == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
    except HTTPException as exc:
        await asyncio.to_thread(destination.unlink, True)
        await audit_action(db, "UPLOAD_EVIDENCE", request=request, user=current_user, table_name="evidence",
                           new_value={"filename": file.filename, "reason": exc.detail, "bytes": written},
                           status="failure", commit=True)
        raise
    except Exception:
        await asyncio.to_thread(destination.unlink, True)
        raise

    file_hash = digest.hexdigest()
    evidence_type = EvidenceType.UPLOADED_VIDEO if kind == "video" else EvidenceType.MANUAL_SNAPSHOT
    duration = await asyncio.to_thread(_probe_duration_seconds, str(destination)) if kind == "video" else None

    evidence = await evidence_crud.create(db, {
        "alert_id": alert_id,
        "camera_id": camera_id,
        "track_id": track_id,
        "evidence_type": evidence_type,
        "file_path": destination.resolve().as_posix(),
        "file_name": destination.name,
        "file_size_bytes": written,
        "file_hash": file_hash,
        "duration_seconds": duration,
        "gps_lat": camera.gps_lat,
        "gps_lng": camera.gps_lng,
    })
    job_id = uuid.uuid4().hex
    await audit_action(db, "UPLOAD_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       record_id=evidence.evidence_id,
                       new_value={"file_name": evidence.file_name, "file_hash": file_hash,
                                  "file_size_bytes": written, "camera_id": camera_id, "job_id": job_id})
    await db.commit()
    return EvidenceUploadResponse(
        evidence_id=evidence.evidence_id,
        file_hash=file_hash,
        file_name=evidence.file_name,
        file_size_bytes=written,
        evidence_type=evidence_type.value,
        job_id=job_id,
        file_url=evidence_service.file_url(evidence.file_path) or "",
        message="Evidence stored and hashed (SHA-256). Queued for ML processing in Week 3.",
    )


@router.get("", response_model=EvidencePage, summary="List evidence")
async def list_evidence(
    request: Request,
    camera_id: Optional[str] = Query(None, max_length=20),
    evidence_type: Optional[EvidenceType] = Query(None),
    alert_id: Optional[str] = Query(None, max_length=40),
    is_hot_storage: Optional[bool] = Query(None),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> EvidencePage:
    conditions = []
    camera_id = normalize_camera_id(camera_id)
    if camera_id:
        conditions.append(Evidence.camera_id == camera_id)
    if evidence_type:
        conditions.append(Evidence.evidence_type == evidence_type)
    if alert_id:
        conditions.append(Evidence.alert_id == alert_id)
    if is_hot_storage is not None:
        conditions.append(Evidence.is_hot_storage.is_(is_hot_storage))
    if date_from:
        conditions.append(Evidence.created_at >= date_from)
    if date_to:
        conditions.append(Evidence.created_at <= date_to)

    query = select(Evidence)
    count_query = select(func.count()).select_from(Evidence)
    for condition in conditions:
        query, count_query = query.where(condition), count_query.where(condition)
    query = restrict_to_visible_cameras(query, Evidence.camera_id, current_user)
    count_query = restrict_to_visible_cameras(count_query, Evidence.camera_id, current_user)

    rows = (await db.execute(
        query.order_by(Evidence.created_at.desc(), Evidence.evidence_id.desc()).offset(skip).limit(limit)
    )).scalars().all()
    total = int((await db.execute(count_query)).scalar() or 0)
    exists_flags = await asyncio.to_thread(lambda: [Path(row.file_path).is_file() for row in rows])

    await audit_action(db, "LIST_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       new_value={"camera_id": camera_id, "returned": len(rows), "total": total})
    await db.commit()
    return EvidencePage(items=[_detail(row, flag) for row, flag in zip(rows, exists_flags)], total=total)


VIDEO_TYPES = (EvidenceType.UPLOADED_VIDEO, EvidenceType.VIDEO_CLIP)


def _job_response(job) -> AnalysisJobResponse:
    return AnalysisJobResponse.model_validate(job.to_dict())


@router.post("/analyze-standalone", response_model=AnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="Analyse a video that belongs to no camera (results only; no alerts or evidence are stored)")
async def analyze_standalone(
    request: Request,
    file: UploadFile = File(..., description="MP4/AVI/MOV/MKV, max 500 MB"),
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJobResponse:
    extension = evidence_service.extension_of(file.filename)
    if evidence_service.classify_extension(extension) != "video":
        await audit_action(db, "VIDEO_ANALYSIS_STARTED", request=request, user=current_user, table_name="evidence",
                           new_value={"filename": file.filename, "reason": "unsupported_type", "standalone": True},
                           status="failure", commit=True)
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only MP4, AVI, MOV or MKV videos can be analysed")
    if analysis_jobs.active_job() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Another video analysis is already running; wait for it to finish")

    directory = standalone_upload_dir()
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    destination = directory / f"{uuid.uuid4().hex}_{evidence_service.sanitize_filename(file.filename)}"
    written = 0
    try:
        async with aiofiles.open(destination, "wb") as target:
            while True:
                chunk = await file.read(COPY_CHUNK_BYTES)
                if not chunk:
                    break
                if written == 0 and not evidence_service.signature_matches(extension, chunk[:16]):
                    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                                        f"File content does not match the .{extension} format")
                written += len(chunk)
                if written > api_settings.upload_max_bytes:
                    raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                        f"Upload exceeds the {api_settings.UPLOAD_MAX_SIZE_MB} MB limit")
                await target.write(chunk)
        if written == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is empty")
        job = await analysis_jobs.start(video_path=str(destination.resolve()), camera_id=None, evidence_id=None,
                                        user=current_user, delete_video_after=True)
    except HTTPException:
        await asyncio.to_thread(destination.unlink, True)
        raise
    except RuntimeError as exc:
        await asyncio.to_thread(destination.unlink, True)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await audit_action(db, "VIDEO_ANALYSIS_STARTED", request=request, user=current_user, table_name="evidence",
                       new_value={"job_id": job.job_id, "standalone": True, "file_name": file.filename, "bytes": written})
    await db.commit()
    return _job_response(job)


@router.get("/analysis-jobs/{job_id}", response_model=AnalysisJobResponse, summary="Progress of a video analysis job")
async def analysis_job_status(
    request: Request,
    job_id: str,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJobResponse:
    job = analysis_jobs.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis job not found")
    if job.camera_id:
        await ensure_camera_access(request, db, current_user, job.camera_id)
    elif str(job.user_id) != str(current_user.user_id) and current_user.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Analysis job not found")
    return _job_response(job)


@router.get("/{evidence_id}", response_model=EvidenceDetailResponse, summary="Evidence detail")
async def get_evidence(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> EvidenceDetailResponse:
    evidence = await evidence_crud.get(db, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence {evidence_id} not found")
    await ensure_camera_access(request, db, current_user, evidence.camera_id)

    exists = await asyncio.to_thread(lambda: Path(evidence.file_path).is_file())
    await audit_action(db, "VIEW_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       record_id=evidence_id)
    await db.commit()
    return _detail(evidence, exists)


@router.get("/{evidence_id}/verify", response_model=EvidenceVerifyResponse,
            summary="Recompute the SHA-256 and compare it with the stored hash")
async def verify_evidence(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> EvidenceVerifyResponse:
    evidence = await evidence_crud.get(db, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence {evidence_id} not found")
    await ensure_camera_access(request, db, current_user, evidence.camera_id)

    path = Path(evidence.file_path)
    if not await asyncio.to_thread(path.is_file):
        computed, integrity = None, "missing"
    else:
        computed = await asyncio.to_thread(evidence_service.compute_sha256, str(path))
        integrity = "valid" if computed == evidence.file_hash else "tampered"

    await audit_action(db, "VERIFY_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       record_id=evidence_id,
                       new_value={"integrity": integrity, "stored_hash": evidence.file_hash, "computed_hash": computed},
                       status="success" if integrity == "valid" else "failure")
    await db.commit()
    if integrity == "tampered":
        logger.error("EVIDENCE TAMPERING DETECTED: evidence_id=%s file=%s", evidence_id, evidence.file_name)
    return EvidenceVerifyResponse(
        evidence_id=evidence_id,
        stored_hash=evidence.file_hash,
        computed_hash=computed,
        integrity=integrity,
        verified_at=datetime.now(timezone.utc),
    )


@router.post("/{evidence_id}/archive", response_model=EvidenceArchiveResponse,
             summary="Move evidence to cold storage (admin only)")
async def archive_evidence(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> EvidenceArchiveResponse:
    try:
        evidence = await evidence_service.archive_evidence(evidence_id, db)
    except (FileNotFoundError, ValueError) as exc:
        await audit_action(db, "ARCHIVE_EVIDENCE", request=request, user=current_user, table_name="evidence",
                           record_id=evidence_id, new_value={"reason": str(exc)}, status="failure", commit=True)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence {evidence_id} not found")

    await audit_action(db, "ARCHIVE_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       record_id=evidence_id,
                       new_value={"is_hot_storage": False, "file_path": evidence.file_path,
                                  "archived_at": evidence.archived_at.isoformat()})
    await db.commit()
    return EvidenceArchiveResponse(
        evidence_id=evidence_id,
        is_hot_storage=evidence.is_hot_storage,
        archived_at=evidence.archived_at,
        file_path=evidence.file_path,
        message="Evidence moved to cold storage (hash verified before the move)",
    )


@router.delete("/{evidence_id}", status_code=status.HTTP_200_OK, summary="Delete evidence (admin only)")
async def delete_evidence(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    evidence = await evidence_crud.get(db, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence {evidence_id} not found")

    before = evidence.to_dict()
    file_path = evidence.file_path
    await db.delete(evidence)
    await db.flush()
    await audit_action(db, "DELETE_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       record_id=evidence_id, old_value=before)
    # The record is removed first: an orphaned file is recoverable, a dangling record is not.
    await db.commit()

    file_deleted = False
    try:
        file_deleted = await evidence_service.delete_file(file_path)
    except (OSError, PermissionError) as exc:
        logger.error("Evidence record %s deleted but file removal failed (%s): %s", evidence_id, file_path, exc)
    return {
        "evidence_id": evidence_id,
        "file_deleted": file_deleted,
        "message": "Evidence deleted" if file_deleted else "Evidence record deleted; file was already absent or locked",
    }


async def _video_evidence(request: Request, db: AsyncSession, user: User, evidence_id: int) -> Evidence:
    evidence = await evidence_crud.get(db, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence {evidence_id} not found")
    await ensure_camera_access(request, db, user, evidence.camera_id)
    if evidence.evidence_type not in VIDEO_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Evidence {evidence_id} is not a video")
    if not await asyncio.to_thread(lambda: Path(evidence.file_path).is_file()):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence file for {evidence_id} is missing from storage")
    return evidence


@router.get("/{evidence_id}/playback", summary="Browser-playable video for this evidence (H.264)",
            response_class=FileResponse)
async def evidence_playback(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Serves the original when the browser can decode it; otherwise a derived H.264 preview. The evidence
    file itself is never modified (its SHA-256 stays valid)."""
    evidence = await _video_evidence(request, db, current_user, evidence_id)
    try:
        path, original = await playable_path_async(evidence.evidence_id, evidence.file_path, evidence.file_hash)
    except (RuntimeError, ValueError) as exc:
        await audit_action(db, "PLAYBACK_EVIDENCE", request=request, user=current_user, table_name="evidence",
                           record_id=evidence_id, new_value={"reason": str(exc)}, status="error", commit=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Video cannot be prepared for playback: {exc}") from exc
    await audit_action(db, "PLAYBACK_EVIDENCE", request=request, user=current_user, table_name="evidence",
                       record_id=evidence_id, new_value={"served": "original" if original else "h264_preview"})
    await db.commit()
    return FileResponse(path, media_type="video/mp4", headers={
        "Cache-Control": "private, no-store",
        "X-Evidence-Playback": "original" if original else "h264-preview",
    })


@router.post("/{evidence_id}/analyze", response_model=AnalysisJobResponse, status_code=status.HTTP_202_ACCEPTED,
             summary="Run ML detection over a video evidence file; high-risk findings become alerts")
async def analyze_evidence(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_supervisor_or_above),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJobResponse:
    evidence = await _video_evidence(request, db, current_user, evidence_id)
    try:
        job = await analysis_jobs.start(video_path=evidence.file_path, camera_id=evidence.camera_id,
                                        evidence_id=evidence.evidence_id, user=current_user,
                                        recorded_at=evidence.created_at)
    except RuntimeError as exc:
        await audit_action(db, "VIDEO_ANALYSIS_STARTED", request=request, user=current_user, table_name="evidence",
                           record_id=evidence_id, new_value={"reason": str(exc)}, status="failure", commit=True)
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await audit_action(db, "VIDEO_ANALYSIS_STARTED", request=request, user=current_user, table_name="evidence",
                       record_id=evidence_id, new_value={"job_id": job.job_id, "camera_id": evidence.camera_id})
    await db.commit()
    return _job_response(job)


@router.get("/{evidence_id}/analysis-status", response_model=AnalysisJobResponse,
            summary="Latest analysis job for this evidence")
async def evidence_analysis_status(
    request: Request,
    evidence_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> AnalysisJobResponse:
    evidence = await evidence_crud.get(db, evidence_id)
    if evidence is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Evidence {evidence_id} not found")
    await ensure_camera_access(request, db, current_user, evidence.camera_id)
    job = analysis_jobs.latest_for_evidence(evidence_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No analysis has been run for evidence {evidence_id}")
    return _job_response(job)

