from typing import List, Optional

from pydantic import Field, model_validator

from backend.core.enums import EvidenceType
from backend.schemas.common import (
    CameraId,
    InputSchema,
    Latitude,
    Longitude,
    NonNegativeInt,
    ORMSchema,
    OptionalTzDatetime,
    Sha256Hex,
    TzDatetime,
)


class EvidenceBase(InputSchema):
    alert_id: Optional[str] = Field(default=None, max_length=40)
    camera_id: CameraId
    track_id: Optional[int] = Field(default=None, ge=0)
    evidence_type: EvidenceType
    file_path: str = Field(min_length=1, max_length=4096)
    file_name: Optional[str] = Field(default=None, max_length=255)
    file_size_bytes: Optional[NonNegativeInt] = None
    file_hash: Sha256Hex
    duration_seconds: Optional[NonNegativeInt] = None
    gps_lat: Optional[Latitude] = None
    gps_lng: Optional[Longitude] = None

    @model_validator(mode="after")
    def _video_duration(self):
        if self.evidence_type in (EvidenceType.SNAPSHOT, EvidenceType.MANUAL_SNAPSHOT) and self.duration_seconds:
            raise ValueError("snapshots cannot have a duration")
        if self.file_name is None:
            self.file_name = self.file_path.replace("\\", "/").rsplit("/", 1)[-1][:255]
        return self


class EvidenceCreate(EvidenceBase):
    pass


class EvidenceUpdate(InputSchema):
    """Only storage lifecycle and linkage may change. Path, hash and size are immutable (chain of custody)."""

    alert_id: Optional[str] = Field(default=None, max_length=40)
    is_hot_storage: Optional[bool] = None
    archived_at: OptionalTzDatetime = None

    @model_validator(mode="after")
    def _archive_consistency(self) -> "EvidenceUpdate":
        if self.is_hot_storage is False and self.archived_at is None:
            raise ValueError("archived_at is required when moving evidence out of hot storage")
        return self


class EvidenceResponse(ORMSchema):
    evidence_id: int
    alert_id: Optional[str] = None
    camera_id: str
    track_id: Optional[int] = None
    evidence_type: EvidenceType
    file_path: str
    file_name: Optional[str] = None
    file_size_bytes: Optional[int] = None
    file_hash: str
    duration_seconds: Optional[int] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    created_at: TzDatetime
    is_hot_storage: bool
    archived_at: OptionalTzDatetime = None


class EvidenceListResponse(ORMSchema):
    items: List[EvidenceResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
