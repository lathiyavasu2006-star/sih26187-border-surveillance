"""Frame annotation in a military HUD style: zones, trails, bracketed boxes, risk and status overlay."""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from ml.event_memory import EventMemory

RISK_COLORS = {
    "normal": (0, 255, 0),        # green
    "low": (255, 255, 0),         # cyan
    "suspicious": (0, 255, 255),  # yellow
    "high_risk": (0, 165, 255),   # orange
    "critical": (0, 0, 255),      # red
}

ZONE_COLORS = {
    "public": (0, 255, 0, 80),
    "buffer": (0, 255, 255, 80),
    "sensitive": (0, 165, 255, 80),
    "restricted": (0, 0, 255, 80),
    "no_mans_land": (0, 0, 180, 120),
}

HUD_GREEN = (0, 255, 0)
FONT = cv2.FONT_HERSHEY_SIMPLEX


class Annotator:
    def draw_frame(self, frame: np.ndarray, detections: List[Dict], memory: EventMemory, zones: List[Dict],
                   camera_id: Optional[str] = None, fps: Optional[float] = None, mode: str = "normal",
                   highlight_track_id: Optional[int] = None) -> np.ndarray:
        annotated = frame.copy()
        annotated = self._draw_zones(annotated, zones)
        for det in detections:
            track_id = det.get("track_id", -1)
            if track_id is not None and track_id >= 0:
                annotated = self._draw_trail(annotated, memory.get_trail(track_id), det.get("risk_level", "normal"))
        for det in detections:
            annotated = self._draw_detection(annotated, det, memory, highlight=det.get("track_id") == highlight_track_id)
        return self._draw_hud(annotated, len(detections), camera_id=camera_id, fps=fps, mode=mode)

    def _draw_zones(self, frame: np.ndarray, zones: List[Dict]) -> np.ndarray:
        overlay = frame.copy()
        outlines = []
        for zone in zones:
            if not zone.get("is_active", True):
                continue
            polygon = zone.get("polygon") or []
            if len(polygon) < 3:
                continue
            pts = np.array(polygon, dtype=np.int32)
            zone_type = zone.get("zone_type", "public")
            color = ZONE_COLORS.get(zone_type, ZONE_COLORS["public"])[:3]
            cv2.fillPoly(overlay, [pts], color)
            outlines.append((pts, color, zone.get("zone_name") or zone_type))
        cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame)
        # Outlines and labels are drawn after blending so they stay at full intensity.
        for pts, color, label in outlines:
            cv2.polylines(frame, [pts], True, color, 2)
            moments = cv2.moments(pts)
            if moments["m00"] != 0:
                cx, cy = int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])
                cv2.putText(frame, str(label).upper(), (max(0, cx - 40), cy), FONT, 0.5, color, 1, cv2.LINE_AA)
        return frame

    def _draw_trail(self, frame: np.ndarray, trail: List[Tuple[int, int]], risk_level: str) -> np.ndarray:
        if len(trail) < 2:
            return frame
        color = RISK_COLORS.get(risk_level, HUD_GREEN)
        for i in range(1, len(trail)):
            fade = i / len(trail)
            faded = tuple(int(c * fade) for c in color)
            cv2.line(frame, tuple(map(int, trail[i - 1])), tuple(map(int, trail[i])), faded, 2, cv2.LINE_AA)
        return frame

    def _draw_detection(self, frame: np.ndarray, det: Dict, memory: EventMemory, highlight: bool = False) -> np.ndarray:
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = (int(det["x1"]), int(det["y1"]), int(det["x2"]), int(det["y2"]))
        track_id = det.get("track_id", -1)
        risk_level = det.get("risk_level", "normal")
        color = RISK_COLORS.get(risk_level, HUD_GREEN)
        thickness = 3 if highlight else 2

        bracket = max(4, min(20, (x2 - x1) // 4, (y2 - y1) // 4))
        for (px, py, dx, dy) in ((x1, y1, 1, 1), (x2, y1, -1, 1), (x1, y2, 1, -1), (x2, y2, -1, -1)):
            cv2.line(frame, (px, py), (px + dx * bracket, py), color, thickness)
            cv2.line(frame, (px, py), (px, py + dy * bracket), color, thickness)

        id_text = f"#{track_id}" if track_id is not None and track_id >= 0 else "#-"
        label = (f"{det.get('cls_name', 'object').upper()} {id_text} | {det.get('confidence', 0):.0%} | "
                 f"{risk_level.upper()}")
        (tw, th), _ = cv2.getTextSize(label, FONT, 0.45, 1)
        label_top = y1 - th - 8 if y1 - th - 8 >= 0 else y2 + 2
        label_x = max(0, min(x1, w - tw - 4))
        cv2.rectangle(frame, (label_x, label_top), (label_x + tw + 4, label_top + th + 8), color, -1)
        cv2.putText(frame, label, (label_x + 2, label_top + th + 3), FONT, 0.45, (0, 0, 0), 1, cv2.LINE_AA)

        if det.get("loitering"):
            cv2.putText(frame, f"LOITERING: {det.get('time_in_zone_seconds', 0)}s", (x1, min(h - 5, y2 + 15)),
                        FONT, 0.45, RISK_COLORS["high_risk"], 1, cv2.LINE_AA)

        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        fence_direction = det.get("fence_direction", det.get("direction"))
        if fence_direction == "toward_fence":
            cv2.arrowedLine(frame, (cx, cy), (cx, max(0, cy - 30)), RISK_COLORS["critical"], 2, tipLength=0.4)
        elif fence_direction == "away_from_fence":
            cv2.arrowedLine(frame, (cx, cy), (cx, min(h - 1, cy + 30)), HUD_GREEN, 2, tipLength=0.4)

        if det.get("weapon_detected"):
            cv2.putText(frame, f"WEAPON: {str(det.get('weapon_class', '')).upper()}", (x1, min(h - 5, y2 + 32)),
                        FONT, 0.5, RISK_COLORS["critical"], 2, cv2.LINE_AA)

        cv2.putText(frame, f"RISK:{det.get('risk_score', 0)}", (min(w - 70, x2 + 5), max(12, y1 + 15)),
                    FONT, 0.45, color, 1, cv2.LINE_AA)
        return frame

    def _draw_hud(self, frame: np.ndarray, detection_count: int, camera_id: Optional[str] = None,
                  fps: Optional[float] = None, mode: str = "normal") -> np.ndarray:
        h, w = frame.shape[:2]
        lines = [datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
        if camera_id:
            lines.append(f"CAM: {camera_id}")
        lines.append(f"OBJECTS: {detection_count}")
        if fps is not None:
            lines.append(f"FPS: {fps:.1f}")
        if mode and mode != "normal":
            lines.append(f"MODE: {mode.upper()}")
        for i, text in enumerate(lines):
            cv2.putText(frame, text, (10, 20 + i * 20), FONT, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
            cv2.putText(frame, text, (10, 20 + i * 20), FONT, 0.5, HUD_GREEN, 1, cv2.LINE_AA)

        bracket = 30
        right, bottom = w - 1, h - 1
        for (px, py, dx, dy) in ((0, 0, 1, 1), (right, 0, -1, 1), (0, bottom, 1, -1), (right, bottom, -1, -1)):
            cv2.line(frame, (px, py), (px + dx * bracket, py), HUD_GREEN, 2)
            cv2.line(frame, (px, py), (px, py + dy * bracket), HUD_GREEN, 2)
        return frame


annotator = Annotator()
