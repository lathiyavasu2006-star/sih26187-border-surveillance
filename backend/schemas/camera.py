from typing import List, Optional

from pydantic import Field, field_validator

from backend.core.enums import CameraStatus, CameraType, ZoneRegion
from backend.core.security import mask_url_credentials
from backend.schemas.common import CameraId, InputSchema, Latitude, Longitude, ORMSchema, TzDatetime

ALLOWED_STREAM_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://", "rtmp://", "udp://", "file://")


def _validate_stream_url(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    if not value.lower().startswith(ALLOWED_STREAM_SCHEMES) and not value.isdigit():
        raise ValueError(f"rtsp_url must start with one of {ALLOWED_STREAM_SCHEMES} or be a local device index")
    return value


class CameraBase(InputSchema):
    name: str = Field(min_length=1, max_length=100)
    location_name: Optional[str] = Field(default=None, max_length=200)
    gps_lat: Optional[Latitude] = None
    gps_lng: Optional[Longitude] = None
    rtsp_url: Optional[str] = Field(default=None, max_length=2048)
    device_id: Optional[str] = Field(default=None, max_length=100)
    camera_type: CameraType = CameraType.STANDARD
    zone_region: ZoneRegion
    sector_name: Optional[str] = Field(default=None, max_length=100)

    _check_url = field_validator("rtsp_url")(_validate_stream_url)


class CameraCreate(CameraBase):
    camera_id: CameraId
    status: CameraStatus = CameraStatus.ONLINE


class CameraUpdate(InputSchema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    location_name: Optional[str] = Field(default=None, max_length=200)
    gps_lat: Optional[Latitude] = None
    gps_lng: Optional[Longitude] = None
    rtsp_url: Optional[str] = Field(default=None, max_length=2048)
    device_id: Optional[str] = Field(default=None, max_length=100)
    camera_type: Optional[CameraType] = None
    zone_region: Optional[ZoneRegion] = None
    sector_name: Optional[str] = Field(default=None, max_length=100)
    status: Optional[CameraStatus] = None

    _check_url = field_validator("rtsp_url")(_validate_stream_url)


class CameraResponse(ORMSchema):
    camera_id: str
    name: str
    location_name: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    rtsp_url: Optional[str] = None
    device_id: Optional[str] = None
    camera_type: CameraType
    zone_region: ZoneRegion
    sector_name: Optional[str] = None
    status: CameraStatus
    registered_at: TzDatetime
    last_seen: TzDatetime
    #: Ground-plane calibration present, so zones drawn on the map project into this camera's pixels.
    calibrated: bool = False
    calibration_error_px: Optional[float] = None

    @field_validator("rtsp_url")
    @classmethod
    def _mask_stream_credentials(cls, value: Optional[str]) -> Optional[str]:
        return mask_url_credentials(value) if value else value


class CameraListResponse(ORMSchema):
    items: List[CameraResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
