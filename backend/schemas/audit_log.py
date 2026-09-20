from ipaddress import ip_address
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, field_validator

from backend.core.enums import AuditStatus
from backend.core.security import mask_sensitive
from backend.schemas.common import InputSchema, ORMSchema, OptionalTzDatetime, TzDatetime


class AuditLogBase(InputSchema):
    user_id: Optional[UUID] = None
    username: Optional[str] = Field(default=None, max_length=50)
    action: str = Field(min_length=1, max_length=100)
    table_name: Optional[str] = Field(default=None, max_length=50)
    record_id: Optional[str] = Field(default=None, max_length=50)
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = Field(default=None, max_length=45)
    user_agent: Optional[str] = None
    session_id: Optional[str] = Field(default=None, max_length=100)
    status: AuditStatus = AuditStatus.SUCCESS

    @field_validator("ip_address")
    @classmethod
    def _valid_ip(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        try:
            return str(ip_address(value))
        except ValueError as exc:
            raise ValueError("ip_address must be a valid IPv4 or IPv6 address") from exc

    @field_validator("user_agent")
    @classmethod
    def _truncate_user_agent(cls, value: Optional[str]) -> Optional[str]:
        return value[:255] if value else value

    @field_validator("old_value", "new_value")
    @classmethod
    def _mask_values(cls, value: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return mask_sensitive(value) if value is not None else None


class AuditLogCreate(AuditLogBase):
    pass


class AuditLogUpdate(InputSchema):
    """Audit rows are append-only in practice; only the outcome may be corrected by an admin."""

    status: Optional[AuditStatus] = None
    record_id: Optional[str] = Field(default=None, max_length=50)
    session_id: Optional[str] = Field(default=None, max_length=100)


class AuditLogFilter(InputSchema):
    user_id: Optional[UUID] = None
    action: Optional[str] = Field(default=None, max_length=100)
    status: Optional[AuditStatus] = None
    date_from: OptionalTzDatetime = None
    date_to: OptionalTzDatetime = None
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class AuditLogResponse(ORMSchema):
    log_id: int
    user_id: Optional[UUID] = None
    username: Optional[str] = None
    action: str
    table_name: Optional[str] = None
    record_id: Optional[str] = None
    old_value: Optional[Dict[str, Any]] = None
    new_value: Optional[Dict[str, Any]] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    status: str
    timestamp: TzDatetime

    @field_validator("old_value", "new_value", mode="before")
    @classmethod
    def _mask_values(cls, value: Any) -> Any:
        return mask_sensitive(value) if value is not None else None


class AuditLogListResponse(ORMSchema):
    items: List[AuditLogResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
