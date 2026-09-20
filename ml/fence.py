"""Virtual fence: zone polygons from the backend (cached 30 s) and point-in-polygon checks."""
import logging
import time
from typing import Dict, List, Optional

import cv2
import numpy as np

from ml.backend_client import BackendClient, backend_client
from ml.config import ml_config

logger = logging.getLogger("sih26187.ml.fence")

ZONE_TYPE_ORDER = {"public": 0, "buffer": 1, "sensitive": 2, "restricted": 3, "no_mans_land": 4}


def empty_zone_result() -> Dict:
    return {
        "in_any_fence": False,
        "zone_id": None,
        "zone_name": None,
        "zone_type": "public",
        "risk_bonus": 0,
    }


class FenceChecker:
    def __init__(self, client: Optional[BackendClient] = None):
        self._client = client or backend_client
        # Cache zones per camera: the backend is not hit on every frame.
        self._zone_cache: Dict[str, List[Dict]] = {}
        self._cache_ttl: Dict[str, float] = {}
        self._cache_duration = ml_config.zone_cache_seconds

    def set_client(self, client: BackendClient) -> None:
        self._client = client

    async def load_zones(self, camera_id: str, force: bool = False) -> List[Dict]:
        now = time.monotonic()
        cached = self._zone_cache.get(camera_id)
        if cached is not None and not force and now - self._cache_ttl.get(camera_id, 0) < self._cache_duration:
            return cached
        try:
            zones = [zone for zone in await self._client.get_zones(camera_id) if zone.get("is_active", True)]
            self._zone_cache[camera_id] = zones
            self._cache_ttl[camera_id] = now
            if cached is None or len(cached) != len(zones):
                logger.info("[%s] %d active zone(s) loaded", camera_id, len(zones))
            return zones
        except Exception as exc:
            # Keep enforcing the last known fence rather than silently dropping it.
            logger.warning("[%s] zone refresh failed (%s); using %d cached zone(s)",
                           camera_id, exc, len(cached or []))
            self._cache_ttl[camera_id] = now  # back off for one cache period
            return cached or []

    def invalidate(self, camera_id: Optional[str] = None) -> None:
        if camera_id is None:
            self._zone_cache.clear()
            self._cache_ttl.clear()
        else:
            self._zone_cache.pop(camera_id, None)
            self._cache_ttl.pop(camera_id, None)

    @staticmethod
    def _polygon(zone: Dict) -> Optional[np.ndarray]:
        polygon = zone.get("polygon") or []
        if len(polygon) < 3:
            return None
        return np.asarray(polygon, dtype=np.float32).reshape(-1, 1, 2)

    def check_point(self, cx: int, cy: int, zones: List[Dict]) -> Dict:
        """Return the highest-risk active zone containing the point (edge counts as inside)."""
        point = (float(cx), float(cy))
        result = empty_zone_result()
        best_rank = (-1, -1)
        for zone in zones:
            if not zone.get("is_active", True):
                continue
            pts = self._polygon(zone)
            if pts is None:
                continue
            if cv2.pointPolygonTest(pts, point, False) < 0:
                continue
            zone_type = zone.get("zone_type", "public")
            risk_bonus = zone.get("risk_bonus")
            if risk_bonus is None:
                risk_bonus = ml_config.zone_risk_bonus.get(zone_type, 0)
            rank = (int(risk_bonus), ZONE_TYPE_ORDER.get(zone_type, 0))
            if rank > best_rank:
                best_rank = rank
                result = {
                    "in_any_fence": True,
                    "zone_id": zone.get("zone_id"),
                    "zone_name": zone.get("zone_name"),
                    "zone_type": zone_type,
                    "risk_bonus": int(risk_bonus),
                }
        return result

    def zone_by_id(self, zones: List[Dict], zone_id) -> Dict:
        for zone in zones:
            if zone.get("zone_id") == zone_id:
                return zone
        return {}

    def most_sensitive_polygon(self, zones: List[Dict]) -> Optional[List]:
        """Polygon of the highest-risk active zone, used as the fence line for direction analysis."""
        candidates = [z for z in zones if z.get("is_active", True) and len(z.get("polygon") or []) >= 3]
        if not candidates:
            return None
        best = max(candidates, key=lambda z: (z.get("risk_bonus") or 0, ZONE_TYPE_ORDER.get(z.get("zone_type"), 0)))
        return best["polygon"]


fence_checker = FenceChecker()
