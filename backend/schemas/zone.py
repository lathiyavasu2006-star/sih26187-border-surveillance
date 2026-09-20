from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator, model_validator

from backend.core.config import settings
from backend.core.enums import ZoneType
from backend.schemas.common import CameraId, HexColor, InputSchema, ORMSchema, TzDatetime

MAX_POLYGON_POINTS = 256


def default_night_rules() -> Dict[str, Any]:
    return {
        "multiplier": settings.NIGHT_RISK_MULTIPLIER,
        "start": settings.NIGHT_START_HOUR,
        "end": settings.NIGHT_END_HOUR,
    }


def validate_polygon(points: List[List[float]]) -> List[List[float]]:
    if len(points) < 3:
        raise ValueError("polygon requires at least 3 points")
    if len(points) > MAX_POLYGON_POINTS:
        raise ValueError(f"polygon supports at most {MAX_POLYGON_POINTS} points")
    cleaned = []
    for index, point in enumerate(points):
        if len(point) != 2:
            raise ValueError(f"polygon point {index} must be [x, y]")
        x, y = float(point[0]), float(point[1])
        if x < 0 or y < 0:
            raise ValueError(f"polygon point {index} must have non-negative pixel coordinates")
        cleaned.append([x, y])
    if len({(p[0], p[1]) for p in cleaned}) < 3:
        raise ValueError("polygon requires at least 3 distinct points")
    return cleaned


def validate_night_rules(rules: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**default_night_rules(), **rules}
    unknown = set(merged) - {"multiplier", "start", "end"}
    if unknown:
        raise ValueError(f"Unknown night_rules keys: {sorted(unknown)}")
    multiplier = float(merged["multiplier"])
    start, end = int(merged["start"]), int(merged["end"])
    if not 1.0 <= multiplier <= 5.0:
        raise ValueError("night_rules.multiplier must be between 1.0 and 5.0")
    if not (0 <= start <= 23 and 0 <= end <= 23):
        raise ValueError("night_rules.start/end must be hours between 0 and 23")
    return {"multiplier": multiplier, "start": start, "end": end}


class ZoneBase(InputSchema):
    zone_name: str = Field(min_length=1, max_length=100)
    zone_type: ZoneType
    polygon: List[List[float]]
    loiter_threshold_seconds: int = Field(default=settings.LOITER_THRESHOLD_SECONDS, ge=1, le=86400)
    risk_bonus: Optional[int] = Field(default=None, ge=0, le=100)
    night_rules: Dict[str, Any] = Field(default_factory=default_night_rules)
    allowed_persons: List[str] = Field(default_factory=list)
    color_hex: HexColor = "#00ff00"
    is_active: bool = True

    _polygon = field_validator("polygon")(validate_polygon)
    _night_rules = field_validator("night_rules")(validate_night_rules)


class ZoneCreate(ZoneBase):
    camera_id: CameraId

    @model_validator(mode="after")
    def _default_risk_bonus(self) -> "ZoneCreate":
        if self.risk_bonus is None:
            self.risk_bonus = settings.ZONE_RISK_BONUS[self.zone_type.value]
        return self


class ZoneUpdate(InputSchema):
    zone_name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    zone_type: Optional[ZoneType] = None
    polygon: Optional[List[List[float]]] = None
    loiter_threshold_seconds: Optional[int] = Field(default=None, ge=1, le=86400)
    risk_bonus: Optional[int] = Field(default=None, ge=0, le=100)
    night_rules: Optional[Dict[str, Any]] = None
    allowed_persons: Optional[List[str]] = None
    color_hex: Optional[HexColor] = None
    is_active: Optional[bool] = None

    @field_validator("polygon")
    @classmethod
    def _polygon(cls, value):
        return validate_polygon(value) if value is not None else value

    @field_validator("night_rules")
    @classmethod
    def _night_rules(cls, value):
        return validate_night_rules(value) if value is not None else value


class ZoneResponse(ORMSchema):
    zone_id: int
    camera_id: str
    zone_name: str
    zone_type: ZoneType
    polygon: List[List[float]] = Field(default_factory=list)
    loiter_threshold_seconds: int
    risk_bonus: int
    night_rules: Dict[str, Any] = Field(default_factory=dict)
    allowed_persons: List[str] = Field(default_factory=list)
    color_hex: str
    is_active: bool
    created_at: TzDatetime


class ZoneListResponse(ORMSchema):
    items: List[ZoneResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
