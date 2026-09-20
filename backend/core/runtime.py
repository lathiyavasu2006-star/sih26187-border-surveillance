"""In-process runtime state: uptime, ML heartbeats and pipeline FPS per camera."""
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Dict, Optional


@dataclass
class CameraHeartbeat:
    last_frame_monotonic: float
    fps: float = 0.0
    people_count: int = 0
    vehicle_count: int = 0
    animal_count: int = 0


@dataclass
class RuntimeState:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_monotonic: float = field(default_factory=time.monotonic)
    _heartbeats: Dict[str, CameraHeartbeat] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def uptime_seconds(self) -> int:
        return int(time.monotonic() - self.started_monotonic)

    def record_frame(self, camera_id: str, fps: float = 0.0, people: int = 0, vehicles: int = 0, animals: int = 0) -> None:
        with self._lock:
            self._heartbeats[camera_id] = CameraHeartbeat(
                last_frame_monotonic=time.monotonic(),
                fps=max(0.0, float(fps or 0.0)),
                people_count=int(people or 0),
                vehicle_count=int(vehicles or 0),
                animal_count=int(animals or 0),
            )

    def seconds_since_frame(self, camera_id: str) -> Optional[float]:
        with self._lock:
            beat = self._heartbeats.get(camera_id)
        return None if beat is None else time.monotonic() - beat.last_frame_monotonic

    def has_heartbeat_history(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._heartbeats

    def forget(self, camera_id: str) -> None:
        with self._lock:
            self._heartbeats.pop(camera_id, None)

    def average_fps(self, max_age_seconds: float) -> float:
        now = time.monotonic()
        with self._lock:
            live = [b.fps for b in self._heartbeats.values() if now - b.last_frame_monotonic <= max_age_seconds]
        return round(sum(live) / len(live), 2) if live else 0.0

    def reset(self) -> None:
        with self._lock:
            self._heartbeats.clear()


runtime_state = RuntimeState()
