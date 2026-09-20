"""Real-time operator notifications over WebSocket."""
from datetime import datetime, timezone
from typing import List, Optional

from backend.websocket.manager import ConnectionManager, manager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotificationService:
    def __init__(self, ws_manager: ConnectionManager):
        self.ws_manager = ws_manager

    async def broadcast_camera_offline(
        self,
        camera_id: str,
        sector_name: Optional[str],
        zone_region: Optional[str],
        alert_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> int:
        sector = sector_name or "unknown sector"
        return await self.ws_manager.broadcast_to_all_operators({
            "type": "camera_offline",
            "severity": "warning",
            "camera_id": camera_id,
            "sector_name": sector_name,
            "zone_region": getattr(zone_region, "value", zone_region),
            "alert_id": alert_id,
            "reason": reason,
            "message": f"ALERT: Camera {camera_id} in {sector} is OFFLINE. Sector may be unsecured.",
            "sound": "alert",
            "timestamp": _now(),
        })

    async def broadcast_camera_online(self, camera_id: str, sector_name: Optional[str]) -> int:
        sector = sector_name or "unknown sector"
        return await self.ws_manager.broadcast_to_all_operators({
            "type": "camera_online",
            "severity": "info",
            "camera_id": camera_id,
            "sector_name": sector_name,
            "message": f"Camera {camera_id} in {sector} is back ONLINE.",
            "timestamp": _now(),
        })

    async def broadcast_critical_alert(
        self,
        alert_id: str,
        camera_id: str,
        risk_score: int,
        risk_reasons: List[str],
        alert_type: Optional[str] = None,
    ) -> int:
        return await self.ws_manager.broadcast_to_all_operators({
            "type": "critical_alert",
            "severity": "critical",
            "alert_id": alert_id,
            "camera_id": camera_id,
            "alert_type": alert_type,
            "risk_score": risk_score,
            "risk_reasons": list(risk_reasons or []),
            "message": f"CRITICAL ALERT on {camera_id} — Risk Score: {risk_score}",
            "sound": "alarm",
            "timestamp": _now(),
        })

    async def broadcast_alert_acknowledged(
        self,
        alert_id: str,
        acknowledged_by: str,
        camera_id: Optional[str] = None,
        false_alarm: bool = False,
        acknowledged_by_username: Optional[str] = None,
    ) -> int:
        return await self.ws_manager.broadcast_to_all_operators({
            "type": "alert_acknowledged",
            "alert_id": alert_id,
            "camera_id": camera_id,
            "acknowledged_by": str(acknowledged_by),
            "acknowledged_by_username": acknowledged_by_username,
            "false_alarm": false_alarm,
            "timestamp": _now(),
        })

    async def broadcast_zone_update(self, camera_id: str, zone_id: int, action: str, zone: Optional[dict] = None) -> int:
        return await self.ws_manager.broadcast_to_camera(camera_id, {
            "type": "zone_update",
            "camera_id": camera_id,
            "zone_id": zone_id,
            "action": action,
            "zone": zone,
            "message": f"Zone {zone_id} {action} on camera {camera_id}",
            "timestamp": _now(),
        })

    async def broadcast_zone_deleted(self, camera_id: str, zone_id: int) -> int:
        return await self.ws_manager.broadcast_to_camera(camera_id, {
            "type": "zone_deleted",
            "camera_id": camera_id,
            "zone_id": zone_id,
            "message": f"Zone {zone_id} deleted on camera {camera_id}",
            "timestamp": _now(),
        })

    async def broadcast_neighbor_offline(self, neighbor_camera_id: str, offline_camera_id: str, sector_name: Optional[str]) -> int:
        return await self.ws_manager.broadcast_to_camera(neighbor_camera_id, {
            "type": "neighbor_offline_alert",
            "offline_camera_id": offline_camera_id,
            "message": (
                f"Neighboring camera {offline_camera_id} in {sector_name or 'unknown sector'} is offline. "
                "Increase monitoring."
            ),
            "timestamp": _now(),
        })


notification_service = NotificationService(manager)
