"""Loitering: dwell time inside a defined zone beyond its threshold (stricter at night)."""
from datetime import datetime
from typing import Dict, Optional

from ml.config import ml_config


class LoiteringDetector:
    def check(self, track_id: int, time_in_zone_seconds: int, zone: Dict,
              now: Optional[datetime] = None) -> Dict:
        """Loitering only applies inside a configured zone; standing anywhere else in view is not loitering.

        At night the zone threshold is divided by the zone's night multiplier (30 s becomes 20 s at x1.5),
        using the zone's own night hours when it defines them."""
        if not zone:
            return {
                "loitering": False,
                "time_in_zone": int(time_in_zone_seconds),
                "threshold_used": None,
                "is_night_rules": False,
            }

        threshold = int(zone.get("loiter_threshold_seconds") or ml_config.default_loiter_threshold)
        rules = zone.get("night_rules") or {}
        is_night = self._is_night(now, rules)
        if is_night:
            if "threshold" in rules:
                threshold = int(rules["threshold"])
            else:
                multiplier = float(rules.get("multiplier", ml_config.night_risk_multiplier) or 1.0)
                if multiplier > 1.0:
                    threshold = max(1, int(round(threshold / multiplier)))

        return {
            "loitering": time_in_zone_seconds >= threshold,
            "time_in_zone": int(time_in_zone_seconds),
            "threshold_used": threshold,
            "is_night_rules": is_night,
        }

    def _is_night(self, now: Optional[datetime] = None, rules: Optional[Dict] = None) -> bool:
        rules = rules or {}
        hour = (now or datetime.now()).astimezone().hour if now and now.tzinfo else (now or datetime.now()).hour
        return ml_config.is_night_hour(hour, rules.get("start"), rules.get("end"))


loitering_detector = LoiteringDetector()
