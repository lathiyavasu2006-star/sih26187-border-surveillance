"""Behavioural risk scoring (0-100) and alert type selection."""
from datetime import datetime
from typing import Dict, Optional

from ml.config import ml_config

LOITERING_POINTS = 20
TOWARD_FENCE_POINTS = 20
REPEAT_APPEARANCE_POINTS = 15
REPEAT_APPEARANCE_MIN = 3
RUNNING_POINTS = 25
WEAPON_POINTS = 50
MAX_SCORE = 100


class RiskEngine:
    def calculate(self, detection: Dict, zone_info: Dict, loitering_result: Dict, direction: str,
                  appear_count: int, time_in_zone: int, now: Optional[datetime] = None) -> Dict:
        score = ml_config.base_risk_score
        reasons = []

        zone_type = zone_info.get("zone_type") or "public"
        zone_bonus = zone_info.get("risk_bonus")
        if zone_bonus is None:
            zone_bonus = ml_config.zone_risk_bonus.get(zone_type, 0)
        zone_bonus = int(zone_bonus)
        if zone_bonus > 0:
            score += zone_bonus
            reasons.append(f"zone_{zone_type}+{zone_bonus}")

        if loitering_result.get("loitering"):
            score += LOITERING_POINTS
            reasons.append(f"loitering+{LOITERING_POINTS}(threshold:{loitering_result.get('threshold_used')}s,"
                           f"dwell:{int(time_in_zone)}s)")

        if direction == "toward_fence":
            score += TOWARD_FENCE_POINTS
            reasons.append(f"toward_fence+{TOWARD_FENCE_POINTS}")

        if appear_count >= REPEAT_APPEARANCE_MIN:
            score += REPEAT_APPEARANCE_POINTS
            reasons.append(f"repeat_appearance+{REPEAT_APPEARANCE_POINTS}(count:{appear_count})")

        if detection.get("is_running"):
            score += RUNNING_POINTS
            reasons.append(f"running+{RUNNING_POINTS}")

        if detection.get("weapon_detected"):
            score += WEAPON_POINTS
            weapon = detection.get("weapon_class") or "weapon"
            reasons.append(f"weapon_detected+{WEAPON_POINTS}({weapon})")

        if self._is_night(now):
            score = int(round(score * ml_config.night_risk_multiplier))
            reasons.append(f"night_multiplier_x{ml_config.night_risk_multiplier}")

        score = max(0, min(int(score), MAX_SCORE))
        return {
            "risk_score": score,
            "risk_level": self._get_level(score),
            "risk_reasons": reasons,
        }

    def _get_level(self, score: int) -> str:
        return ml_config.risk_level_for(score)

    def _is_night(self, now: Optional[datetime] = None) -> bool:
        return ml_config.is_night_hour((now or datetime.now()).astimezone().hour if now and now.tzinfo
                                       else (now or datetime.now()).hour)

    def should_create_alert(self, risk_level: str) -> bool:
        return risk_level in ml_config.alert_risk_levels

    def alert_type_for(self, detection: Dict, zone_info: Dict, loitering_result: Dict) -> str:
        """Map behaviour to a backend AlertType value (the enum rejects anything else)."""
        cls_name = detection.get("cls_name", "person")
        zone_type = zone_info.get("zone_type") or "public"
        if detection.get("weapon_detected"):
            return "weapon"
        if ml_config.is_animal(cls_name):
            return "animal"
        if loitering_result.get("loitering"):
            return "loitering"
        if zone_info.get("in_any_fence") and zone_type in ("restricted", "no_mans_land"):
            return "vehicle" if ml_config.is_vehicle(cls_name) else "intrusion"
        if zone_info.get("in_any_fence") and zone_type in ("buffer", "sensitive"):
            return "zone_breach"
        if ml_config.is_vehicle(cls_name):
            return "vehicle"
        return "behavior"


risk_engine = RiskEngine()
