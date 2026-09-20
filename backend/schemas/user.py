from typing import List, Optional
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from backend.core.enums import UserRole, ZoneRegion
from backend.core.security import validate_password_strength
from backend.schemas.common import InputSchema, NonNegativeInt, ORMSchema, OptionalTzDatetime, TzDatetime

USERNAME_PATTERN = r"^[A-Za-z0-9_.-]{3,50}$"


def _unique(values: List) -> List:
    seen, result = set(), []
    for item in values:
        key = getattr(item, "value", item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


class UserBase(InputSchema):
    username: str = Field(pattern=USERNAME_PATTERN)
    role: UserRole
    camera_access: List[str] = Field(default_factory=list)
    zone_access: List[ZoneRegion] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("username")
    @classmethod
    def _normalise_username(cls, value: str) -> str:
        return value.lower()

    @field_validator("camera_access", "zone_access")
    @classmethod
    def _dedupe(cls, value: List) -> List:
        return _unique(value)


class UserCreate(UserBase):
    # Plain password is accepted only on input, hashed by the CRUD layer, and never echoed back.
    password: str = Field(min_length=12, max_length=72, repr=False)

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def _password_not_username(self) -> "UserCreate":
        if self.username and self.username.lower() in self.password.lower():
            raise ValueError("Password must not contain the username")
        return self


class UserUpdate(InputSchema):
    username: Optional[str] = Field(default=None, pattern=USERNAME_PATTERN)
    role: Optional[UserRole] = None
    camera_access: Optional[List[str]] = None
    zone_access: Optional[List[ZoneRegion]] = None
    is_active: Optional[bool] = None

    @field_validator("username")
    @classmethod
    def _normalise_username(cls, value: Optional[str]) -> Optional[str]:
        return value.lower() if value else value

    @field_validator("camera_access", "zone_access")
    @classmethod
    def _dedupe(cls, value: Optional[List]) -> Optional[List]:
        return _unique(value) if value is not None else value


class UserPasswordChange(InputSchema):
    current_password: str = Field(min_length=1, max_length=72, repr=False)
    new_password: str = Field(min_length=12, max_length=72, repr=False)

    @field_validator("new_password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def _must_differ(self) -> "UserPasswordChange":
        if self.current_password == self.new_password:
            raise ValueError("New password must differ from the current password")
        return self


class UserResponse(ORMSchema):
    """Public user representation. password_hash is deliberately absent."""

    user_id: UUID
    username: str
    role: UserRole
    camera_access: List[str] = Field(default_factory=list)
    zone_access: List[str] = Field(default_factory=list)
    is_active: bool
    created_at: TzDatetime
    last_login: OptionalTzDatetime = None
    failed_login_attempts: NonNegativeInt = 0
    locked_until: OptionalTzDatetime = None


class UserListResponse(ORMSchema):
    items: List[UserResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
