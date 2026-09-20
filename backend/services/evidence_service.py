"""Evidence file handling: hashing, snapshots, uploads, archival, secure paths."""
import asyncio
import hashlib
import hmac
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.api_config import api_settings
from backend.core.config import settings
from backend.database.crud import evidence_crud
from backend.models import Evidence

HASH_CHUNK_BYTES = 8192
SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")

# Leading bytes ("magic numbers") of accepted formats. Extension alone is never trusted.
_SIGNATURES = {
    "jpg": [(0, b"\xff\xd8\xff")],
    "jpeg": [(0, b"\xff\xd8\xff")],
    "png": [(0, b"\x89PNG\r\n\x1a\n")],
    "mp4": [(4, b"ftyp")],
    "mov": [(4, b"ftyp"), (4, b"moov"), (4, b"wide"), (4, b"mdat"), (4, b"free")],
    "avi": [(0, b"RIFF")],
    "mkv": [(0, b"\x1a\x45\xdf\xa3")],
}


class EvidenceService:
    # ---------------------------------------------------------------- paths

    @property
    def evidence_root(self) -> Path:
        return Path(settings.EVIDENCE_PATH).resolve()

    @property
    def uploads_dir(self) -> Path:
        return self.evidence_root / "uploads"

    @property
    def cold_storage_dir(self) -> Path:
        return self.evidence_root / "cold_storage"

    def ensure_directories(self) -> None:
        settings.ensure_directories()
        for directory in (self.uploads_dir, self.cold_storage_dir, Path(settings.SNAPSHOTS_PATH), Path(settings.CLIPS_PATH)):
            directory.mkdir(parents=True, exist_ok=True)

    def is_within_evidence_root(self, file_path: str) -> bool:
        try:
            Path(file_path).resolve().relative_to(self.evidence_root)
            return True
        except (ValueError, OSError):
            return False

    def file_url(self, file_path: str) -> Optional[str]:
        """URL under the authenticated /evidence/files mount, or None if the file is outside the evidence root."""
        try:
            relative = Path(file_path).resolve().relative_to(self.evidence_root)
        except (ValueError, OSError):
            return None
        return "/evidence/files/" + relative.as_posix()

    @staticmethod
    def sanitize_filename(filename: str, max_length: int = 150) -> str:
        name = Path(filename or "").name  # strips any directory component (path traversal)
        name = SAFE_NAME_PATTERN.sub("_", name).strip("._") or "file"
        stem, dot, ext = name.rpartition(".")
        if dot and len(name) > max_length:
            return f"{stem[: max_length - len(ext) - 1]}.{ext}"
        return name[:max_length]

    # ---------------------------------------------------------------- hashing

    def compute_sha256(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(HASH_CHUNK_BYTES), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def verify_integrity(self, file_path: str, stored_hash: str) -> bool:
        if not Path(file_path).is_file():
            return False
        computed = self.compute_sha256(file_path)
        return hmac.compare_digest(computed, stored_hash or "")

    def get_file_size(self, file_path: str) -> int:
        return Path(file_path).stat().st_size if Path(file_path).exists() else 0

    # ---------------------------------------------------------------- validation

    def extension_of(self, filename: str) -> str:
        return Path(filename or "").suffix.lower().lstrip(".")

    def classify_extension(self, extension: str) -> Optional[str]:
        if extension in api_settings.video_extensions:
            return "video"
        if extension in api_settings.image_extensions:
            return "image"
        return None

    @staticmethod
    def signature_matches(extension: str, header: bytes) -> bool:
        signatures = _SIGNATURES.get(extension)
        if not signatures:
            return False
        if extension == "avi" and not (header[:4] == b"RIFF" and header[8:12] == b"AVI "):
            return False
        return any(header[offset: offset + len(magic)] == magic for offset, magic in signatures)

    # ---------------------------------------------------------------- writes

    async def save_snapshot(self, frame_bytes: bytes, alert_id: str, camera_id: str) -> Tuple[str, str]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", alert_id or "") or not re.fullmatch(r"[A-Za-z0-9_-]{2,20}", camera_id or ""):
            raise ValueError("Unsafe alert_id or camera_id for snapshot filename")
        if not frame_bytes.startswith(b"\xff\xd8\xff"):
            raise ValueError("Snapshot frame is not a JPEG image")
        directory = Path(settings.SNAPSHOTS_PATH)
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"{alert_id}_{camera_id}.jpg"

        def _write() -> str:
            digest = hashlib.sha256(frame_bytes).hexdigest()
            with open(file_path, "xb") as f:  # "x": never silently overwrite existing evidence
                f.write(frame_bytes)
            return digest

        file_hash = await asyncio.to_thread(_write)
        return file_path.resolve().as_posix(), file_hash

    def new_upload_path(self, original_filename: str) -> Path:
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        return self.uploads_dir / f"{uuid.uuid4().hex}_{self.sanitize_filename(original_filename)}"

    async def archive_evidence(self, evidence_id: int, db: AsyncSession) -> Optional[Evidence]:
        """Verify, move the file to cold storage and mark the record archived. Returns None if not found.

        Raises FileNotFoundError if the file is missing and ValueError if its hash no longer matches."""
        evidence = await evidence_crud.get(db, evidence_id)
        if evidence is None:
            return None
        if not evidence.is_hot_storage:
            raise ValueError(f"Evidence {evidence_id} is already archived")
        source = Path(evidence.file_path)
        if not source.is_file():
            raise FileNotFoundError(f"Evidence file missing on disk: {source.name}")
        if not await asyncio.to_thread(self.verify_integrity, str(source), evidence.file_hash):
            raise ValueError(f"Evidence {evidence_id} failed integrity check; refusing to archive tampered file")

        self.cold_storage_dir.mkdir(parents=True, exist_ok=True)
        destination = self.cold_storage_dir / source.name
        if destination.exists():
            destination = self.cold_storage_dir / f"{evidence_id}_{source.name}"
        await asyncio.to_thread(shutil.move, str(source), str(destination))
        try:
            await evidence_crud.update(db, evidence, {
                "file_path": destination.resolve().as_posix(),
                "is_hot_storage": False,
                "archived_at": datetime.now(timezone.utc),
            })
        except Exception:
            await asyncio.to_thread(shutil.move, str(destination), str(source))  # keep disk and DB consistent
            raise
        return evidence

    async def delete_file(self, file_path: str) -> bool:
        path = Path(file_path)
        if not path.exists():
            return False
        if not self.is_within_evidence_root(file_path):
            raise PermissionError("Refusing to delete a file outside the evidence root")
        await asyncio.to_thread(path.unlink)
        return True


evidence_service = EvidenceService()
