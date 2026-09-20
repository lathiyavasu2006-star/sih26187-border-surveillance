"""Shared building blocks for the Pydantic v2 schemas."""
from typing import Annotated, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

# Inputs reject unknown fields so typos never silently drop data.
class InputSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=False,
    )


# Outputs are built from ORM objects and only expose declared fields.
class ORMSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="ignore")


CameraId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,19}$", to_upper=True)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]
RiskScore = Annotated[int, Field(ge=0, le=100)]
NonNegativeInt = Annotated[int, Field(ge=0)]
Percent = Annotated[float, Field(ge=0, le=100)]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{64}$", to_lower=True)]
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
TzDatetime = AwareDatetime
OptionalTzDatetime = Optional[AwareDatetime]


class PaginationParams(InputSchema):
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)
