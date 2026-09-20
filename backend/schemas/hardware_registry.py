from typing import Any, Dict, List, Optional

from pydantic import Field, field_validator

from backend.core.enums import HardwareStatus, HardwareType
from backend.core.security import mask_sensitive
from backend.schemas.common import CameraId, InputSchema, ORMSchema, OptionalTzDatetime, TzDatetime


class HardwareBase(InputSchema):
    hardware_type: HardwareType
    name: str = Field(min_length=1, max_length=100)
    model_number: Optional[str] = Field(default=None, max_length=100)
    manufacturer: Optional[str] = Field(default=None, max_length=100)
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    status: HardwareStatus = HardwareStatus.STANDBY
    camera_id: Optional[CameraId] = None
    capabilities: List[str] = Field(default_factory=list)
    firmware_version: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=5000)

    @field_validator("capabilities")
    @classmethod
    def _dedupe_capabilities(cls, value: List[str]) -> List[str]:
        return list(dict.fromkeys(value))


class HardwareCreate(HardwareBase):
    # Repr hides credentials if the object is ever logged.
    connection_config: Dict[str, Any] = Field(default_factory=dict, repr=False)


class HardwareUpdate(InputSchema):
    hardware_type: Optional[HardwareType] = None
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    model_number: Optional[str] = Field(default=None, max_length=100)
    manufacturer: Optional[str] = Field(default=None, max_length=100)
    connection_config: Optional[Dict[str, Any]] = Field(default=None, repr=False)
    status: Optional[HardwareStatus] = None
    camera_id: Optional[CameraId] = None
    capabilities: Optional[List[str]] = None
    last_seen: OptionalTzDatetime = None
    firmware_version: Optional[str] = Field(default=None, max_length=50)
    notes: Optional[str] = Field(default=None, max_length=5000)


class HardwareResponse(ORMSchema):
    hardware_id: int
    hardware_type: HardwareType
    name: str
    model_number: Optional[str] = None
    manufacturer: Optional[str] = None
    connection_config: Dict[str, Any] = Field(default_factory=dict)
    status: HardwareStatus
    camera_id: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)
    last_seen: OptionalTzDatetime = None
    firmware_version: Optional[str] = None
    registered_at: TzDatetime
    notes: Optional[str] = None

    @field_validator("connection_config", mode="before")
    @classmethod
    def _mask_credentials(cls, value: Any) -> Any:
        # Masked at construction time so the secret never exists on the response object.
        return mask_sensitive(value or {})


class HardwareListResponse(ORMSchema):
    items: List[HardwareResponse] = Field(default_factory=list)
    total: int = Field(ge=0)


# Aliases matching the table/model name.
HardwareRegistryBase = HardwareBase
HardwareRegistryCreate = HardwareCreate
HardwareRegistryUpdate = HardwareUpdate
HardwareRegistryResponse = HardwareResponse
HardwareRegistryListResponse = HardwareListResponse
