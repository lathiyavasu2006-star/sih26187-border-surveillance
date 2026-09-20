from typing import List, Optional

from pydantic import Field, model_validator

from backend.core.enums import Direction, RiskLevel, ZoneType
from backend.schemas.common import CameraId, InputSchema, NonNegativeInt, ORMSchema, OptionalTzDatetime, RiskScore, TzDatetime


class TrackedObjectBase(InputSchema):
    track_id: int = Field(ge=0)
    camera_id: CameraId
    object_class: str = Field(min_length=1, max_length=20)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    bbox_x1: Optional[NonNegativeInt] = None
    bbox_y1: Optional[NonNegativeInt] = None
    bbox_x2: Optional[NonNegativeInt] = None
    bbox_y2: Optional[NonNegativeInt] = None
    cx: Optional[NonNegativeInt] = None
    cy: Optional[NonNegativeInt] = None
    in_fence: bool = False
    zone_name: Optional[str] = Field(default=None, max_length=100)
    zone_type: Optional[ZoneType] = None
    loitering: bool = False
    time_in_zone_seconds: NonNegativeInt = 0
    direction: Direction = Direction.STATIONARY
    risk_score: RiskScore = 0
    risk_level: RiskLevel = RiskLevel.NORMAL

    @model_validator(mode="after")
    def _bbox(self):
        coords = (self.bbox_x1, self.bbox_y1, self.bbox_x2, self.bbox_y2)
        if any(c is not None for c in coords):
            if any(c is None for c in coords):
                raise ValueError("bbox requires all of bbox_x1, bbox_y1, bbox_x2, bbox_y2")
            if self.bbox_x2 < self.bbox_x1 or self.bbox_y2 < self.bbox_y1:
                raise ValueError("bbox_x2/bbox_y2 must be >= bbox_x1/bbox_y1")
            if self.cx is None:
                self.cx = (self.bbox_x1 + self.bbox_x2) // 2
            if self.cy is None:
                self.cy = (self.bbox_y1 + self.bbox_y2) // 2
        return self


class TrackedObjectCreate(TrackedObjectBase):
    timestamp: OptionalTzDatetime = None


class TrackedObjectUpdate(InputSchema):
    object_class: Optional[str] = Field(default=None, min_length=1, max_length=20)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    bbox_x1: Optional[NonNegativeInt] = None
    bbox_y1: Optional[NonNegativeInt] = None
    bbox_x2: Optional[NonNegativeInt] = None
    bbox_y2: Optional[NonNegativeInt] = None
    cx: Optional[NonNegativeInt] = None
    cy: Optional[NonNegativeInt] = None
    in_fence: Optional[bool] = None
    zone_name: Optional[str] = Field(default=None, max_length=100)
    zone_type: Optional[ZoneType] = None
    loitering: Optional[bool] = None
    time_in_zone_seconds: Optional[NonNegativeInt] = None
    direction: Optional[Direction] = None
    risk_score: Optional[RiskScore] = None
    risk_level: Optional[RiskLevel] = None


class TrackedObjectResponse(ORMSchema):
    id: int
    track_id: int
    camera_id: str
    object_class: str
    confidence: Optional[float] = None
    bbox_x1: Optional[int] = None
    bbox_y1: Optional[int] = None
    bbox_x2: Optional[int] = None
    bbox_y2: Optional[int] = None
    cx: Optional[int] = None
    cy: Optional[int] = None
    in_fence: bool
    zone_name: Optional[str] = None
    zone_type: Optional[str] = None
    loitering: bool
    time_in_zone_seconds: int
    direction: str
    risk_score: int
    risk_level: str
    timestamp: TzDatetime


class TrackedObjectListResponse(ORMSchema):
    items: List[TrackedObjectResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
