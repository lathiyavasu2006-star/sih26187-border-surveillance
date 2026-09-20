from typing import List, Optional

from pydantic import Field, model_validator

from backend.schemas.common import InputSchema, NonNegativeInt, ORMSchema, Percent, TzDatetime


class SystemHealthBase(InputSchema):
    server_name: str = Field(default="local", min_length=1, max_length=50)
    region: Optional[str] = Field(default=None, max_length=20)
    cpu_percent: Optional[Percent] = None
    ram_percent: Optional[Percent] = None
    ram_used_gb: Optional[float] = Field(default=None, ge=0, lt=10000)
    ram_total_gb: Optional[float] = Field(default=None, ge=0, lt=10000)
    gpu_percent: Optional[Percent] = None
    gpu_memory_used_mb: Optional[NonNegativeInt] = None
    gpu_memory_total_mb: Optional[NonNegativeInt] = None
    disk_used_gb: Optional[float] = Field(default=None, ge=0, lt=1000000)
    disk_free_gb: Optional[float] = Field(default=None, ge=0, lt=1000000)
    avg_fps: float = Field(default=0, ge=0, lt=1000)
    cameras_online: NonNegativeInt = 0
    cameras_total: NonNegativeInt = 0
    active_alerts: NonNegativeInt = 0
    critical_alerts: NonNegativeInt = 0
    persons_detected: NonNegativeInt = 0
    vehicles_detected: NonNegativeInt = 0

    @model_validator(mode="after")
    def _consistency(self):
        if self.cameras_online > self.cameras_total:
            raise ValueError("cameras_online cannot exceed cameras_total")
        if self.critical_alerts > self.active_alerts:
            raise ValueError("critical_alerts cannot exceed active_alerts")
        if (
            self.ram_used_gb is not None
            and self.ram_total_gb is not None
            and self.ram_used_gb > self.ram_total_gb
        ):
            raise ValueError("ram_used_gb cannot exceed ram_total_gb")
        if (
            self.gpu_memory_used_mb is not None
            and self.gpu_memory_total_mb is not None
            and self.gpu_memory_used_mb > self.gpu_memory_total_mb
        ):
            raise ValueError("gpu_memory_used_mb cannot exceed gpu_memory_total_mb")
        return self


class SystemHealthCreate(SystemHealthBase):
    pass


class SystemHealthUpdate(InputSchema):
    region: Optional[str] = Field(default=None, max_length=20)
    cpu_percent: Optional[Percent] = None
    ram_percent: Optional[Percent] = None
    ram_used_gb: Optional[float] = Field(default=None, ge=0, lt=10000)
    ram_total_gb: Optional[float] = Field(default=None, ge=0, lt=10000)
    gpu_percent: Optional[Percent] = None
    gpu_memory_used_mb: Optional[NonNegativeInt] = None
    gpu_memory_total_mb: Optional[NonNegativeInt] = None
    disk_used_gb: Optional[float] = Field(default=None, ge=0, lt=1000000)
    disk_free_gb: Optional[float] = Field(default=None, ge=0, lt=1000000)
    avg_fps: Optional[float] = Field(default=None, ge=0, lt=1000)
    cameras_online: Optional[NonNegativeInt] = None
    cameras_total: Optional[NonNegativeInt] = None
    active_alerts: Optional[NonNegativeInt] = None
    critical_alerts: Optional[NonNegativeInt] = None
    persons_detected: Optional[NonNegativeInt] = None
    vehicles_detected: Optional[NonNegativeInt] = None


class SystemHealthResponse(ORMSchema):
    id: int
    server_name: str
    region: Optional[str] = None
    cpu_percent: Optional[float] = None
    ram_percent: Optional[float] = None
    ram_used_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None
    gpu_percent: Optional[float] = None
    gpu_memory_used_mb: Optional[int] = None
    gpu_memory_total_mb: Optional[int] = None
    disk_used_gb: Optional[float] = None
    disk_free_gb: Optional[float] = None
    avg_fps: float
    cameras_online: int
    cameras_total: int
    active_alerts: int
    critical_alerts: int
    persons_detected: int
    vehicles_detected: int
    timestamp: TzDatetime


class SystemHealthListResponse(ORMSchema):
    items: List[SystemHealthResponse] = Field(default_factory=list)
    total: int = Field(ge=0)
