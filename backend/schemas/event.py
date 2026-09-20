from typing import Any, Dict, List, Optional

from pydantic import Field, model_validator

from backend.schemas.common import CameraId, InputSchema, NonNegativeInt, ORMSchema, OptionalTzDatetime, RiskScore, TzDatetime


class EventBase(InputSchema):
    track_id: int = Field(ge=0)
    person_uuid: Optional[str] = Field(default=None, max_length=64)
    camera_id: CameraId
    object_class: str = Field(default="person", min_length=1, max_length=20)
    first_seen: TzDatetime
    last_seen: TzDatetime
    total_time_seconds: NonNegativeInt = 0
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    alert_count: NonNegativeInt = 0
    max_risk_score: RiskScore = 0
    zone_history: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool = True

    @model_validator(mode="after")
    def _timing(self):
        if self.last_seen < self.first_seen:
            raise ValueError("last_seen must not be earlier than first_seen")
        if not self.total_time_seconds:
            self.total_time_seconds = int((self.last_seen - self.first_seen).total_seconds())
        return self


class EventCreate(EventBase):
    pass


class EventUpdate(InputSchema):
    person_uuid: Optional[str] = Field(default=None, max_length=64)
    object_class: Optional[str] = Field(default=None, min_length=1, max_length=20)
    last_seen: OptionalTzDatetime = None
    total_time_seconds: Optional[NonNegativeInt] = None
    positions: Optional[List[Dict[str, Any]]] = None
    alert_count: Optional[NonNegativeInt] = None
    max_risk_score: Optional[RiskScore] = None
    zone_history: Optional[List[Dict[str, Any]]] = None
    is_active: Optional[bool] = None


class EventResponse(ORMSchema):
    event_id: int
    track_id: int
    person_uuid: Optional[str] = None
    camera_id: str
    object_class: str
    first_seen: TzDatetime
    last_seen: TzDatetime
    total_time_seconds: int
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    alert_count: int
    max_risk_score: int
    zone_history: List[Dict[str, Any]] = Field(default_factory=list)
    is_active: bool


class EventListResponse(ORMSchema):
    items: List[EventResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
