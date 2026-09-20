"""Domain enumerations shared by SQLAlchemy models, Pydantic schemas and migrations.

Each enum maps to a native PostgreSQL ENUM type whose name is listed in PG_ENUM_NAMES.
"""
from enum import Enum
from typing import Dict, List, Type


class CameraType(str, Enum):
    STANDARD = "standard"
    PTZ = "ptz"
    THERMAL = "thermal"
    DRONE = "drone"
    RADAR = "radar"
    SCANNER = "scanner"
    SATELLITE = "satellite"


class ZoneRegion(str, Enum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"


class CameraStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class UserRole(str, Enum):
    ADMIN = "admin"
    REGIONAL_HEAD = "regional_head"
    SUPERVISOR = "supervisor"
    OPERATOR = "operator"


class ZoneType(str, Enum):
    PUBLIC = "public"
    BUFFER = "buffer"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"
    NO_MANS_LAND = "no_mans_land"


class AlertType(str, Enum):
    INTRUSION = "intrusion"
    LOITERING = "loitering"
    WEAPON = "weapon"
    VEHICLE = "vehicle"
    BEHAVIOR = "behavior"
    CAMERA_OFFLINE = "camera_offline"
    ZONE_BREACH = "zone_breach"
    ANIMAL = "animal"
    SMOKE = "smoke"
    FENCE_DAMAGE = "fence_damage"


class RiskLevel(str, Enum):
    NORMAL = "normal"
    LOW = "low"
    SUSPICIOUS = "suspicious"
    HIGH_RISK = "high_risk"
    CRITICAL = "critical"


class EvidenceType(str, Enum):
    SNAPSHOT = "snapshot"
    VIDEO_CLIP = "video_clip"
    MANUAL_SNAPSHOT = "manual_snapshot"
    UPLOADED_VIDEO = "uploaded_video"


class HardwareType(str, Enum):
    STANDARD_CAMERA = "standard_camera"
    PTZ_CAMERA = "ptz_camera"
    THERMAL_CAMERA = "thermal_camera"
    DRONE = "drone"
    RADAR = "radar"
    LICENSE_PLATE_SCANNER = "license_plate_scanner"
    SATELLITE = "satellite"


class HardwareStatus(str, Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"
    STANDBY = "standby"


class Direction(str, Enum):
    """Movement direction of a tracked object (stored as VARCHAR, not a PG enum)."""

    STATIONARY = "stationary"
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    NORTHEAST = "northeast"
    NORTHWEST = "northwest"
    SOUTHEAST = "southeast"
    SOUTHWEST = "southwest"


class AuditStatus(str, Enum):
    """Outcome of an audited action (stored as VARCHAR, not a PG enum)."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
    ERROR = "error"


PG_ENUM_NAMES: Dict[Type[Enum], str] = {
    CameraType: "camera_type_enum",
    ZoneRegion: "zone_region_enum",
    CameraStatus: "camera_status_enum",
    UserRole: "user_role_enum",
    ZoneType: "zone_type_enum",
    AlertType: "alert_type_enum",
    RiskLevel: "risk_level_enum",
    EvidenceType: "evidence_type_enum",
    HardwareType: "hardware_type_enum",
    HardwareStatus: "hardware_status_enum",
}


def enum_values(enum_cls: Type[Enum]) -> List[str]:
    return [member.value for member in enum_cls]
