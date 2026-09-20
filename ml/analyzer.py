"""Per-frame behavioural analysis shared by live pipelines and uploaded-video processing.

For each tracked object: zone membership, dwell time, loitering, fence-relative direction, running,
weapon attribution, re-appearance, risk score, and alert decisions with a per-(track, alert type) cooldown.
Keeping one implementation guarantees a live camera and an uploaded clip are judged identically.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ml.config import ml_config
from ml.direction import compass_heading, direction_detector
from ml.event_memory import EventMemory
from ml.fence import fence_checker
from ml.loitering import loitering_detector
from ml.risk_engine import risk_engine

MIN_SPEED_WINDOW_SECONDS = 0.5
WEAPON_BOX_EXPANSION = 0.25


def generate_alert_id(now: Optional[datetime] = None) -> str:
    """ALT-YYYYMMDDHHMMSS-<16 hex>: 35 chars, matches the backend alert id pattern and sorts by time."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"ALT-{now.strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:16]}"


@dataclass
class AlertCandidate:
    alert_id: str
    alert_type: str
    track_id: int
    detection: Dict
    zone_info: Dict
    risk: Dict
    created_at: datetime


@dataclass
class FrameAnalysis:
    detections: List[Dict] = field(default_factory=list)
    alerts: List[AlertCandidate] = field(default_factory=list)
    people_count: int = 0
    vehicle_count: int = 0
    animal_count: int = 0
    weapon_count: int = 0


def split_weapons(detections: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    """Weapons are attributed to people rather than tracked as independent objects."""
    trackable, weapons = [], []
    for det in detections:
        (weapons if ml_config.is_weapon(det.get("cls_name", "")) else trackable).append(det)
    return trackable, weapons


def _expanded(det: Dict, ratio: float) -> Tuple[float, float, float, float]:
    w, h = det["x2"] - det["x1"], det["y2"] - det["y1"]
    return det["x1"] - w * ratio, det["y1"] - h * ratio, det["x2"] + w * ratio, det["y2"] + h * ratio


def attribute_weapons(people: List[Dict], weapons: List[Dict]) -> None:
    """Mark the person whose (slightly expanded) box contains the weapon centre and is nearest to it."""
    for weapon in weapons:
        if weapon.get("confidence", 0) < ml_config.conf_threshold:
            continue
        best, best_distance = None, None
        for person in people:
            x1, y1, x2, y2 = _expanded(person, WEAPON_BOX_EXPANSION)
            if x1 <= weapon["cx"] <= x2 and y1 <= weapon["cy"] <= y2:
                distance = (person["cx"] - weapon["cx"]) ** 2 + (person["cy"] - weapon["cy"]) ** 2
                if best_distance is None or distance < best_distance:
                    best, best_distance = person, distance
        if best is not None:
            best["weapon_detected"] = True
            best["weapon_class"] = weapon["cls_name"]
            best["weapon_confidence"] = weapon["confidence"]


class FrameAnalyzer:
    def __init__(self, camera_id: str, memory: EventMemory):
        self.camera_id = camera_id
        self.memory = memory

    def analyze(self, tracked: List[Dict], weapons: List[Dict], zones: List[Dict],
                now: Optional[datetime] = None) -> FrameAnalysis:
        now = now or datetime.now(timezone.utc)
        analysis = FrameAnalysis(weapon_count=len(weapons))
        people = [d for d in tracked if ml_config.is_person(d.get("cls_name", ""))]
        attribute_weapons(people, weapons)
        fence_polygon = fence_checker.most_sensitive_polygon(zones)

        for det in tracked:
            track_id = int(det.get("track_id", -1))
            if track_id < 0:
                continue
            cls_name = det.get("cls_name", "unknown")
            if ml_config.is_person(cls_name):
                analysis.people_count += 1
            elif ml_config.is_vehicle(cls_name):
                analysis.vehicle_count += 1
            elif ml_config.is_animal(cls_name):
                analysis.animal_count += 1

            # Ground position (bottom centre) decides zone membership: zones are drawn on the ground.
            foot_x, foot_y = det["cx"], det["y2"]
            zone_info = fence_checker.check_point(foot_x, foot_y, zones)
            bbox_height = max(1, det["y2"] - det["y1"])

            td = self.memory.update(track_id, self.camera_id, det["cx"], det["cy"], zone_info, 0,
                                    cls_name, now=now, bbox_height=bbox_height)
            time_in_zone = td.time_in_current_zone
            current_zone = fence_checker.zone_by_id(zones, zone_info.get("zone_id")) if zone_info["in_any_fence"] else {}
            loiter_result = loitering_detector.check(track_id, time_in_zone, current_zone, now=now)

            fence_direction = direction_detector.compute(track_id, self.memory, fence_polygon)
            trail = self.memory.get_trail(track_id)
            heading = compass_heading(trail[-min(len(trail), 8)], trail[-1]) if len(trail) >= 2 else "stationary"

            span = (td.timed_trail[-1][0] - td.timed_trail[0][0]).total_seconds() if len(td.timed_trail) >= 2 else 0.0
            speed = self.memory.get_speed(track_id, window_seconds=1.0)
            det["is_running"] = bool(
                ml_config.is_person(cls_name)
                and span >= MIN_SPEED_WINDOW_SECONDS
                and speed / bbox_height >= ml_config.running_speed_heights_per_second
            )
            det["speed_px_per_second"] = round(speed, 1)

            appear_count = self.memory.get_appear_count(track_id)
            risk = risk_engine.calculate(det, zone_info, loiter_result, fence_direction, appear_count,
                                         time_in_zone, now=now)
            td.max_risk_score = max(td.max_risk_score, risk["risk_score"])
            if td.positions:
                td.positions[-1]["risk"] = risk["risk_score"]

            det.update({
                "in_fence": zone_info["in_any_fence"],
                "zone_id": zone_info.get("zone_id"),
                "zone_name": zone_info.get("zone_name"),
                "zone_type": zone_info.get("zone_type", "public"),
                "loitering": loiter_result["loitering"],
                "time_in_zone_seconds": int(time_in_zone),
                "direction": heading,
                "fence_direction": fence_direction,
                "appear_count": appear_count,
                "risk_score": risk["risk_score"],
                "risk_level": risk["risk_level"],
                "risk_reasons": risk["risk_reasons"],
            })
            analysis.detections.append(det)

            if risk_engine.should_create_alert(risk["risk_level"]):
                alert_type = risk_engine.alert_type_for(det, zone_info, loiter_result)
                if self.memory.can_alert(track_id, alert_type, ml_config.alert_cooldown_seconds, now=now):
                    self.memory.record_alert(track_id, alert_type, now=now)
                    analysis.alerts.append(AlertCandidate(
                        alert_id=generate_alert_id(now),
                        alert_type=alert_type,
                        track_id=track_id,
                        detection=det,
                        zone_info=zone_info,
                        risk=risk,
                        created_at=now,
                    ))
        return analysis


# ---------------------------------------------------------------------------- backend payload mapping

def to_ws_detection(det: Dict) -> Dict:
    """Shape a detection exactly as the backend WSDetection schema expects."""
    return {
        "track_id": int(det["track_id"]),
        "object_class": str(det.get("cls_name", "unknown"))[:20],
        "confidence": round(min(1.0, max(0.0, float(det.get("confidence", 0.0)))), 3),
        "cx": max(0, int(det["cx"])),
        "cy": max(0, int(det["cy"])),
        "bbox_x1": max(0, int(det["x1"])),
        "bbox_y1": max(0, int(det["y1"])),
        "bbox_x2": max(0, int(det["x2"])),
        "bbox_y2": max(0, int(det["y2"])),
        "in_fence": bool(det.get("in_fence", False)),
        "zone_name": det.get("zone_name"),
        "zone_type": det.get("zone_type") if det.get("in_fence") else None,
        "loitering": bool(det.get("loitering", False)),
        "time_in_zone_seconds": max(0, int(det.get("time_in_zone_seconds", 0))),
        "direction": det.get("direction", "stationary"),
        "risk_score": max(0, min(100, int(det.get("risk_score", 0)))),
        "risk_level": det.get("risk_level", "normal"),
    }


def to_ws_alert(candidate: AlertCandidate, snapshot_path: Optional[str]) -> Dict:
    zone_info = candidate.zone_info
    return {
        "alert_id": candidate.alert_id,
        "alert_type": candidate.alert_type,
        "risk_score": candidate.risk["risk_score"],
        "risk_level": candidate.risk["risk_level"],
        "risk_reasons": list(candidate.risk["risk_reasons"]),
        "snapshot_path": snapshot_path,
        "track_id": candidate.track_id,
        "zone_name": zone_info.get("zone_name") if zone_info.get("in_any_fence") else None,
        "zone_type": zone_info.get("zone_type") if zone_info.get("in_any_fence") else None,
    }
