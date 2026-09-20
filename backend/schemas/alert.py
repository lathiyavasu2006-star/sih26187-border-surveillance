from typing import List, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.core.config import settings
from backend.core.enums import AlertType, RiskLevel, ZoneType
from backend.models.alert import generate_alert_id
from backend.schemas.common import (
    CameraId,
    InputSchema,
    Latitude,
    Longitude,
    ORMSchema,
    OptionalTzDatetime,
    RiskScore,
    TzDatetime,
)

ALERT_ID_PATTERN = r"^[A-Za-z0-9_-]{8,40}$"


class AlertBase(InputSchema):
    camera_id: CameraId
    track_id: Optional[int] = Field(default=None, ge=0)
    person_uuid: Optional[str] = Field(default=None, max_length=64)
    alert_type: AlertType
    risk_score: RiskScore
    risk_level: Optional[RiskLevel] = None
    risk_reasons: List[str] = Field(default_factory=list)
    snapshot_path: Optional[str] = None
    video_clip_path: Optional[str] = None
    gps_lat: Optional[Latitude] = None
    gps_lng: Optional[Longitude] = None
    zone_name: Optional[str] = Field(default=None, max_length=100)
    zone_type: Optional[ZoneType] = None

    @model_validator(mode="after")
    def _risk_level_matches_score(self):
        expected = RiskLevel(settings.get_risk_level(self.risk_score))
        if self.risk_level is None:
            self.risk_level = expected
        elif self.risk_level != expected:
            raise ValueError(
                f"risk_level '{self.risk_level.value}' does not match risk_score {self.risk_score} "
                f"(expected '{expected.value}')"
            )
        return self


class AlertCreate(AlertBase):
    alert_id: str = Field(default_factory=generate_alert_id, pattern=ALERT_ID_PATTERN)
    timestamp: OptionalTzDatetime = None


class AlertUpdate(InputSchema):
    risk_score: Optional[RiskScore] = None
    risk_level: Optional[RiskLevel] = None
    risk_reasons: Optional[List[str]] = None
    snapshot_path: Optional[str] = None
    video_clip_path: Optional[str] = None
    zone_name: Optional[str] = Field(default=None, max_length=100)
    zone_type: Optional[ZoneType] = None
    false_alarm: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _risk_level_matches_score(self):
        if self.risk_score is not None:
            expected = RiskLevel(settings.get_risk_level(self.risk_score))
            if self.risk_level is None:
                self.risk_level = expected
            elif self.risk_level != expected:
                raise ValueError("risk_level does not match risk_score")
        return self


class AlertAcknowledge(InputSchema):
    acknowledged_by: UUID
    false_alarm: bool = False
    notes: Optional[str] = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _false_alarm_needs_note(self) -> "AlertAcknowledge":
        if self.false_alarm and not self.notes:
            raise ValueError("notes are required when marking an alert as a false alarm")
        return self


class AlertFilter(InputSchema):
    camera_id: Optional[str] = Field(default=None, max_length=20)
    alert_type: Optional[AlertType] = None
    risk_level: Optional[RiskLevel] = None
    date_from: OptionalTzDatetime = None
    date_to: OptionalTzDatetime = None
    acknowledged: Optional[bool] = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def _date_range(self) -> "AlertFilter":
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from must be earlier than or equal to date_to")
        return self


class AlertResponse(ORMSchema):
    alert_id: str
    camera_id: str
    track_id: Optional[int] = None
    person_uuid: Optional[str] = None
    alert_type: AlertType
    risk_score: int
    risk_level: RiskLevel
    risk_reasons: List[str] = Field(default_factory=list)
    snapshot_path: Optional[str] = None
    video_clip_path: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    timestamp: TzDatetime
    acknowledged: bool
    acknowledged_by: Optional[UUID] = None
    acknowledged_at: OptionalTzDatetime = None
    false_alarm: bool
    notes: Optional[str] = None

    @field_validator("risk_reasons", mode="before")
    @classmethod
    def _none_to_list(cls, value):
        return value or []


class AlertListResponse(ORMSchema):
    items: List[AlertResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
    unacknowledged_count: int = Field(ge=0)
