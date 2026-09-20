"""Authenticated static serving of evidence files.

A plain StaticFiles mount would publish every snapshot and clip of the border to anyone who guesses a
filename, so this subclass authenticates the request (Bearer header or ?token= for <img> tags), enforces
camera-level access through the evidence record, and writes a DOWNLOAD_EVIDENCE audit row.
"""
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse

from backend.core.auth import resolve_user_from_token, user_can_access_camera
from backend.core.config import settings
from backend.core.enums import UserRole
from backend.database.database import get_db_context
from backend.models import Camera, Evidence
from backend.services.audit import audit_action

logger = logging.getLogger("sih26187.evidence_files")


def _extract_token(request: Request) -> Optional[str]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.query_params.get("token")


class SecureEvidenceFiles(StaticFiles):
    def __init__(self) -> None:
        super().__init__(directory=None, packages=None, html=False, check_dir=False)

    @property
    def root(self) -> Path:
        return Path(settings.EVIDENCE_PATH).resolve()

    def lookup_path(self, path: str):
        """Resolve inside the current evidence root, rejecting traversal outside it."""
        root = str(self.root)
        full_path = os.path.realpath(os.path.join(root, path))
        if os.path.commonpath([full_path, root]) != root:
            return "", None
        try:
            return full_path, os.stat(full_path)
        except (FileNotFoundError, NotADirectoryError, ValueError, OSError):
            return "", None

    async def _authorize(self, request: Request, relative_path: str):
        token = _extract_token(request)
        if not token:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        resolved, _ = self.lookup_path(relative_path)
        async with get_db_context() as db:
            try:
                user, _claims = await resolve_user_from_token(db, token)
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

            allowed, evidence_id, camera_id = False, None, None
            if resolved:
                record = (
                    await db.execute(
                        select(Evidence, Camera)
                        .join(Camera, Camera.camera_id == Evidence.camera_id)
                        .where(Evidence.file_name == Path(resolved).name)
                    )
                ).all()
                for evidence, camera in record:
                    try:
                        same_file = Path(evidence.file_path).resolve() == Path(resolved).resolve()
                    except OSError:
                        same_file = False
                    if same_file:
                        evidence_id, camera_id = evidence.evidence_id, camera.camera_id
                        allowed = user_can_access_camera(user, camera.camera_id, camera.zone_region)
                        break
                else:
                    # File with no evidence record (e.g. an orphan on disk): admin-only.
                    allowed = user.role == UserRole.ADMIN

            await audit_action(
                db, "DOWNLOAD_EVIDENCE", request=request, user=user, table_name="evidence",
                record_id=evidence_id,
                new_value={"path": relative_path, "camera_id": camera_id, "found": bool(resolved)},
                status="success" if allowed and resolved else "denied",
                commit=True,
            )
            if not allowed:
                return JSONResponse({"detail": "No access to this evidence file"}, status_code=403)
        return None

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await super().__call__(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.method not in ("GET", "HEAD"):
            response = JSONResponse({"detail": "Method not allowed"}, status_code=405)
            await response(scope, receive, send)
            return
        try:
            denial = await self._authorize(request, self.get_path(scope))
        except Exception:
            logger.exception("Evidence file authorization failed")
            denial = JSONResponse({"detail": "Internal server error"}, status_code=500)
        if denial is not None:
            await denial(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


secure_evidence_files = SecureEvidenceFiles()
