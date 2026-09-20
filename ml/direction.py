"""Movement direction relative to the fence, plus an image-relative compass heading.

Fence-relative direction uses the signed distance to the fence polygon (positive inside) at the start
and end of the recent trail, which is far more reliable than comparing against the polygon centroid:
a person walking along a long fence line is "parallel", not "toward".
"""
import math
from typing import List, Optional, Tuple

import cv2
import numpy as np

from ml.event_memory import EventMemory

RECENT_POINTS = 8
MIN_MOVEMENT_PX = 2.0
MOVING_SPEED_PX = 5.0
APPROACH_RATIO = 0.5

COMPASS = ["east", "northeast", "north", "northwest", "west", "southwest", "south", "southeast"]


def compass_heading(start: Tuple[int, int], end: Tuple[int, int], min_movement: float = MIN_MOVEMENT_PX) -> str:
    """8-way heading in image coordinates (up = north). Returned values match the backend Direction enum."""
    dx = end[0] - start[0]
    dy = start[1] - end[1]  # image y grows downward
    if math.hypot(dx, dy) < min_movement:
        return "stationary"
    angle = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    return COMPASS[int((angle + 22.5) // 45) % 8]


class DirectionDetector:
    def compute(self, track_id: int, memory: EventMemory, fence_polygon: Optional[List] = None) -> str:
        trail = memory.get_trail(track_id)
        return self.compute_from_trail(trail, fence_polygon)

    def compute_from_trail(self, trail: List[Tuple[int, int]], fence_polygon: Optional[List] = None) -> str:
        if len(trail) < 3:
            return "stationary"
        recent = trail[-RECENT_POINTS:]
        start = np.array(recent[0], dtype=np.float32)
        end = np.array(recent[-1], dtype=np.float32)
        movement = float(np.linalg.norm(end - start))
        if movement < MIN_MOVEMENT_PX:
            return "stationary"

        if fence_polygon and len(fence_polygon) >= 3:
            pts = np.array(fence_polygon, dtype=np.float32).reshape(-1, 1, 2)
            d_start = cv2.pointPolygonTest(pts, (float(start[0]), float(start[1])), True)
            d_end = cv2.pointPolygonTest(pts, (float(end[0]), float(end[1])), True)
            approach = d_end - d_start  # > 0 means getting deeper into / closer to the zone
            if approach > APPROACH_RATIO * movement:
                return "toward_fence"
            if approach < -APPROACH_RATIO * movement:
                return "away_from_fence"
            return "parallel"

        return "moving" if movement > MOVING_SPEED_PX else "stationary"

    def heading(self, track_id: int, memory: EventMemory) -> str:
        trail = memory.get_trail(track_id)
        if len(trail) < 2:
            return "stationary"
        recent = trail[-RECENT_POINTS:]
        return compass_heading(recent[0], recent[-1])


direction_detector = DirectionDetector()
