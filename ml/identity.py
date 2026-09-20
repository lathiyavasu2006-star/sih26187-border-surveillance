"""Stable per-camera identities on top of ByteTrack (appearance re-identification).

ByteTrack forgets a track after `track_buffer` frames without a match. A person who steps out of view,
is occluded, or is missed for a moment in low light comes back with a NEW tracker id, so one individual
became dozens of "persons today". This resolver maps raw tracker ids to stable identity ids:

* while a tracker id is alive it keeps its identity;
* a new tracker id is compared with identities that were lost within `window_seconds` and are of the
  same class; the closest appearance (HSV colour histograms of the upper and lower body) wins when it is
  similar enough, and a spatial bonus favours reappearing near the last position;
* otherwise a new identity is created.

It is a colour-appearance model, not a deep ReID network: two people dressed alike can be merged and a
person who changes clothes gets a new id. Thresholds are configurable (ML_REID_*).
"""
import itertools
import math
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import cv2
import numpy as np

from ml.config import ml_config

H_BINS = 18
S_BINS = 8
DESCRIPTOR_SIZE = (48, 96)  # width, height after resizing the crop
MIN_CROP_PIXELS = 12 * 24
EMA = 0.2  # how fast a live identity's descriptor follows appearance changes


def appearance_descriptor(frame: np.ndarray, box) -> Optional[np.ndarray]:
    """Two L1-normalised H-S histograms (upper, lower body) of the box, or None for tiny/empty crops."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1 or (x2 - x1) * (y2 - y1) < MIN_CROP_PIXELS:
        return None
    # Trim 10 % on each side: the box edges are mostly background.
    pad_x = int((x2 - x1) * 0.1)
    crop = frame[y1:y2, x1 + pad_x: max(x1 + pad_x + 1, x2 - pad_x)]
    crop = cv2.resize(crop, DESCRIPTOR_SIZE, interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Ignore near-grey and very dark pixels whose hue is noise.
    mask = cv2.inRange(hsv, (0, 30, 30), (180, 255, 255))
    half = hsv.shape[0] // 2
    parts = []
    for rows in (slice(0, half), slice(half, hsv.shape[0])):
        part, part_mask = hsv[rows], mask[rows]
        if cv2.countNonZero(part_mask) < 20:
            part_mask = None  # mostly grey clothing: fall back to every pixel
        hist = cv2.calcHist([part], [0, 1], part_mask, [H_BINS, S_BINS], [0, 180, 0, 256]).flatten()
        total = float(hist.sum())
        parts.append(hist / total if total > 0 else hist)
    return np.concatenate(parts).astype(np.float32)


def appearance_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Mean Bhattacharyya distance of the two body halves (0 = identical, 1 = unrelated)."""
    size = H_BINS * S_BINS
    distances = [
        cv2.compareHist(a[i * size:(i + 1) * size], b[i * size:(i + 1) * size], cv2.HISTCMP_BHATTACHARYYA)
        for i in range(2)
    ]
    return float(sum(distances) / 2)


@dataclass
class Identity:
    identity_id: int
    cls_name: str
    descriptor: Optional[np.ndarray]
    last_seen: float
    last_center: tuple
    frame_diagonal: float
    raw_ids: set = field(default_factory=set)


class IdentityResolver:
    def __init__(self, camera_id: str, window_seconds: Optional[float] = None, max_distance: Optional[float] = None,
                 clock=time.monotonic):
        self.camera_id = camera_id
        self.window_seconds = ml_config.reid_window_seconds if window_seconds is None else window_seconds
        self.max_distance = ml_config.reid_max_distance if max_distance is None else max_distance
        self.clock = clock
        self._ids = itertools.count(1)
        self._by_raw: Dict[int, Identity] = {}
        self._identities: Dict[int, Identity] = {}
        self.reidentified = 0

    def _candidates(self, cls_name: str, now: float, busy: set) -> List[Identity]:
        return [
            identity for identity in self._identities.values()
            if identity.identity_id not in busy
            and identity.cls_name == cls_name
            and now - identity.last_seen <= self.window_seconds
        ]

    def _match(self, descriptor: Optional[np.ndarray], center, cls_name: str, now: float, busy: set,
               diagonal: float) -> Optional[Identity]:
        if descriptor is None:
            return None
        best, best_score = None, self.max_distance
        for identity in self._candidates(cls_name, now, busy):
            if identity.descriptor is None:
                continue
            score = appearance_distance(descriptor, identity.descriptor)
            # Reappearing close to where the identity was last seen makes the match more likely.
            gap = math.hypot(center[0] - identity.last_center[0], center[1] - identity.last_center[1])
            score -= 0.05 * max(0.0, 1.0 - gap / max(1.0, 0.25 * diagonal))
            if score <= best_score:
                best, best_score = identity, score
        return best

    def resolve(self, frame: Optional[np.ndarray], tracked: List[Dict]) -> List[Dict]:
        """Rewrites each detection's track_id to its stable identity id (the raw id is kept as raw_track_id)."""
        now = self.clock()
        diagonal = math.hypot(*frame.shape[:2][::-1]) if frame is not None else 1000.0
        live_raw = {int(det["track_id"]) for det in tracked}
        busy = {self._by_raw[raw].identity_id for raw in live_raw if raw in self._by_raw}
        resolved: List[Dict] = []
        for det in tracked:
            raw = int(det["track_id"])
            cls_name = det.get("cls_name", "person")
            center = (det.get("cx", 0), det.get("cy", 0))
            box = (det.get("x1", 0), det.get("y1", 0), det.get("x2", 0), det.get("y2", 0))
            descriptor = appearance_descriptor(frame, box) if frame is not None else None
            identity = self._by_raw.get(raw)
            if identity is None:
                identity = self._match(descriptor, center, cls_name, now, busy, diagonal)
                if identity is not None:
                    self.reidentified += 1
                else:
                    identity = Identity(next(self._ids), cls_name, descriptor, now, center, diagonal)
                    self._identities[identity.identity_id] = identity
                identity.raw_ids.add(raw)
                self._by_raw[raw] = identity
                busy.add(identity.identity_id)
            if descriptor is not None:
                identity.descriptor = descriptor if identity.descriptor is None else (
                    (1 - EMA) * identity.descriptor + EMA * descriptor
                ).astype(np.float32)
            identity.last_seen = now
            identity.last_center = center
            enriched = dict(det)
            enriched["raw_track_id"] = raw
            enriched["track_id"] = identity.identity_id
            resolved.append(enriched)
        self._forget(now)
        return resolved

    def _forget(self, now: float) -> None:
        expired = [key for key, identity in self._identities.items() if now - identity.last_seen > self.window_seconds]
        for key in expired:
            identity = self._identities.pop(key)
            for raw in identity.raw_ids:
                self._by_raw.pop(raw, None)

    @property
    def identity_count(self) -> int:
        return len(self._identities)
