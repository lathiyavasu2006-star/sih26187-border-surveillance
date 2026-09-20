"""Per-track behavioural memory: trail, zone dwell time, re-appearance, alert cooldowns.

Every time-dependent method accepts an optional `now`. Live cameras use wall-clock time; uploaded
videos pass the video timestamp so dwell times and speeds stay correct when a file is processed faster
or slower than real time.
"""
import math
from collections import deque
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from ml.config import ml_config

MAX_POSITIONS = 1000
POSITIONS_KEEP = 500


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrackData:
    def __init__(self, track_id: int, camera_id: str, now: Optional[datetime] = None):
        now = now or _utcnow()
        self.track_id = track_id
        self.camera_id = camera_id
        self.first_seen = now
        self.last_seen = now
        self.positions: List[Dict] = []
        self.trail: Deque[Tuple[int, int]] = deque(maxlen=ml_config.trail_length)
        self.timed_trail: Deque[Tuple[datetime, int, int]] = deque(maxlen=ml_config.trail_length)
        self.total_time_seconds: int = 0
        self.alert_count: int = 0
        self.appear_count: int = 1
        self.max_risk_score: int = 0
        self.zone_history: List[Dict] = []
        self.current_zone: Optional[str] = None
        self.current_zone_type: str = "public"
        self.zone_entry_time: Optional[datetime] = None
        self.time_in_current_zone: int = 0
        self.is_active: bool = True
        self.last_alert_times: Dict[str, datetime] = {}
        self.object_class: str = "person"
        self.last_bbox_height: int = 0
        self.reappearance_consumed: bool = False  # history already inherited by a newer track

    @property
    def last_position(self) -> Optional[Tuple[int, int]]:
        return self.trail[-1] if self.trail else None


class EventMemory:
    def __init__(self):
        self._tracks: Dict[int, TrackData] = {}

    def __len__(self) -> int:
        return len(self._tracks)

    def __contains__(self, track_id: int) -> bool:
        return track_id in self._tracks

    def get(self, track_id: int) -> Optional[TrackData]:
        return self._tracks.get(track_id)

    def get_or_create(self, track_id: int, camera_id: str, object_class: str = "person",
                      now: Optional[datetime] = None) -> TrackData:
        if track_id not in self._tracks:
            now = now or _utcnow()
            td = TrackData(track_id, camera_id, now)
            td.object_class = object_class
            self._tracks[track_id] = td
        return self._tracks[track_id]

    def _inherit_reappearance(self, td: TrackData, cx: int, cy: int, now: datetime) -> None:
        """A new track id close to where a recently lost track of the same class vanished is treated as the
        same individual returning. This is a spatial-temporal heuristic, not appearance re-identification."""
        best: Optional[TrackData] = None
        best_distance = float(ml_config.reappear_radius_px)
        for other in self._tracks.values():
            if (other is td or other.is_active or other.reappearance_consumed
                    or other.object_class != td.object_class or not other.trail):
                continue
            if (now - other.last_seen).total_seconds() > ml_config.reappear_window_seconds:
                continue
            ox, oy = other.trail[-1]
            distance = math.hypot(cx - ox, cy - oy)
            if distance <= best_distance:
                best, best_distance = other, distance
        if best is not None:
            td.appear_count = best.appear_count + 1
            td.alert_count = best.alert_count
            td.max_risk_score = max(td.max_risk_score, best.max_risk_score)
            best.reappearance_consumed = True  # the history now lives on the new track

    def update(self, track_id: int, camera_id: str, cx: int, cy: int,
               zone_info: Dict, risk_score: int, object_class: str = "person",
               now: Optional[datetime] = None, bbox_height: int = 0) -> TrackData:
        now = now or _utcnow()
        is_new = track_id not in self._tracks
        td = self.get_or_create(track_id, camera_id, object_class, now)

        if is_new:
            self._inherit_reappearance(td, cx, cy, now)
        elif not td.is_active:
            td.is_active = True          # ByteTrack re-activated a lost id
            td.appear_count += 1

        td.last_seen = now
        td.trail.append((int(cx), int(cy)))
        td.timed_trail.append((now, int(cx), int(cy)))
        if bbox_height:
            td.last_bbox_height = int(bbox_height)

        zone_type = zone_info.get("zone_type") or "public"
        td.positions.append({
            "t": now.isoformat(),
            "cx": int(cx),
            "cy": int(cy),
            "zone": zone_type,
            "risk": int(risk_score),
        })
        if len(td.positions) > MAX_POSITIONS:
            td.positions = td.positions[-POSITIONS_KEEP:]
        td.total_time_seconds = int((now - td.first_seen).total_seconds())

        zone_name = zone_info.get("zone_name")
        if zone_name != td.current_zone:
            if td.current_zone and td.zone_entry_time:
                td.zone_history.append({
                    "zone_name": td.current_zone,
                    "zone_type": td.current_zone_type,
                    "entered": td.zone_entry_time.isoformat(),
                    "exited": now.isoformat(),
                    "duration_seconds": int((now - td.zone_entry_time).total_seconds()),
                })
            td.current_zone = zone_name
            td.current_zone_type = zone_type
            td.zone_entry_time = now
            td.time_in_current_zone = 0
        elif td.zone_entry_time:
            td.time_in_current_zone = int((now - td.zone_entry_time).total_seconds())

        td.max_risk_score = max(td.max_risk_score, int(risk_score))
        return td

    # ------------------------------------------------------------------ queries

    def get_time_in_zone(self, track_id: int, now: Optional[datetime] = None) -> int:
        td = self._tracks.get(track_id)
        if not td or not td.zone_entry_time:
            return 0
        if now is None:
            return td.time_in_current_zone
        return max(0, int((now - td.zone_entry_time).total_seconds()))

    def get_appear_count(self, track_id: int) -> int:
        td = self._tracks.get(track_id)
        return td.appear_count if td else 1

    def get_trail(self, track_id: int) -> List[Tuple[int, int]]:
        td = self._tracks.get(track_id)
        return list(td.trail) if td else []

    def get_speed(self, track_id: int, window_seconds: float = 1.0) -> float:
        """Pixels per second over the most recent `window_seconds` of the trail."""
        td = self._tracks.get(track_id)
        if not td or len(td.timed_trail) < 2:
            return 0.0
        end_time, end_x, end_y = td.timed_trail[-1]
        start = td.timed_trail[0]
        for sample in reversed(td.timed_trail):
            if (end_time - sample[0]).total_seconds() >= window_seconds:
                start = sample
                break
        elapsed = (end_time - start[0]).total_seconds()
        if elapsed <= 0:
            return 0.0
        return math.hypot(end_x - start[1], end_y - start[2]) / elapsed

    # ------------------------------------------------------------------ alerts

    def can_alert(self, track_id: int, alert_type: str, cooldown_seconds: int,
                  now: Optional[datetime] = None) -> bool:
        td = self._tracks.get(track_id)
        if not td:
            return True
        last = td.last_alert_times.get(alert_type)
        if not last:
            return True
        return ((now or _utcnow()) - last).total_seconds() >= cooldown_seconds

    def record_alert(self, track_id: int, alert_type: str, now: Optional[datetime] = None) -> None:
        td = self._tracks.get(track_id)
        if td:
            td.alert_count += 1
            td.last_alert_times[alert_type] = now or _utcnow()

    # ------------------------------------------------------------------ lifecycle

    def mark_lost(self, track_id: int) -> None:
        td = self._tracks.get(track_id)
        if td:
            td.is_active = False

    def mark_missing_as_lost(self, seen_track_ids, now: Optional[datetime] = None) -> List[int]:
        """Mark active tracks that have not been seen for `lost_track_seconds` as lost."""
        now = now or _utcnow()
        lost = []
        for track_id, td in self._tracks.items():
            if td.is_active and track_id not in seen_track_ids:
                if (now - td.last_seen).total_seconds() >= ml_config.lost_track_seconds:
                    td.is_active = False
                    lost.append(track_id)
        return lost

    def get_active_tracks(self) -> List[TrackData]:
        return [td for td in self._tracks.values() if td.is_active]

    def cleanup_old_tracks(self, max_age_seconds: int = 300, now: Optional[datetime] = None) -> int:
        now = now or _utcnow()
        stale = [
            tid for tid, td in self._tracks.items()
            if not td.is_active and (now - td.last_seen).total_seconds() > max_age_seconds
        ]
        for tid in stale:
            del self._tracks[tid]
        return len(stale)


event_memory = EventMemory()
