"""Per-camera ByteTrack on top of the detector output.

Why not `model.track(persist=True)` on the shared model:
* it runs YOLO a second time on every frame (the detector already ran), halving throughput;
* the tracker state lives on the shared predictor, so every camera would feed one tracker and track ids
  would jump between cameras.

This wrapper keeps one BYTETracker per camera and feeds it the existing detections. It also fixes an
Ultralytics behaviour that breaks multi-camera use: track ids come from a process-wide counter that every
new BYTETracker resets to zero, so starting camera B would make camera A reuse ids that are still live.
Here every tracker owns its own id counter.
"""
import itertools
import logging
from typing import Dict, List, Optional

import numpy as np

from ml.config import ml_config

logger = logging.getLogger("sih26187.ml.tracker")

TRACK_LOW_THRESH = 0.1
NEW_TRACK_MARGIN = 0.05


class _TrackInput:
    """Minimal stand-in for ultralytics `Boxes` exposing exactly what BYTETracker.update reads."""

    def __init__(self, xyxy: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xyxy = xyxy.astype(np.float32)
        self.conf = conf.astype(np.float32)
        self.cls = cls.astype(np.float32)
        xywh = np.empty_like(self.xyxy)
        xywh[:, 0] = (self.xyxy[:, 0] + self.xyxy[:, 2]) / 2
        xywh[:, 1] = (self.xyxy[:, 1] + self.xyxy[:, 3]) / 2
        xywh[:, 2] = self.xyxy[:, 2] - self.xyxy[:, 0]
        xywh[:, 3] = self.xyxy[:, 3] - self.xyxy[:, 1]
        self.xywh = xywh

    def __len__(self) -> int:
        return len(self.conf)


def _tracker_args(frame_rate: float):
    from ultralytics.utils import IterableSimpleNamespace

    # Tracks start at track_new_conf; conf_threshold stays the bar for weapon attribution and alerts.
    high = float(ml_config.track_new_conf)
    return IterableSimpleNamespace(
        tracker_type="bytetrack",
        track_high_thresh=high,
        track_low_thresh=min(TRACK_LOW_THRESH, high),
        new_track_thresh=min(0.95, high),
        track_buffer=int(ml_config.track_buffer),
        match_thresh=float(ml_config.match_threshold),
        fuse_score=True,
    )


def _build_camera_tracker(frame_rate: float):
    from ultralytics.trackers.byte_tracker import BYTETracker, STrack

    class CameraSTrack(STrack):
        """STrack whose ids come from its owning tracker instead of the global counter."""

        id_source = None

        def next_id(self):  # instance method shadows BaseTrack.next_id (a staticmethod)
            return next(self.id_source)

    class CameraBYTETracker(BYTETracker):
        def __init__(self, args, frame_rate=30):
            self._id_counter = itertools.count(1)
            self._strack_cls = type("BoundCameraSTrack", (CameraSTrack,), {"id_source": self._id_counter})
            super().__init__(args, frame_rate)

        def reset_id(self):  # instance-level: never touches other cameras' counters
            self._id_counter = itertools.count(1)
            self._strack_cls.id_source = self._id_counter

        def init_track(self, dets, scores, cls, img=None):
            if not len(dets):
                return []
            return [self._strack_cls(xywh, s, c) for (xywh, s, c) in zip(dets, scores, cls)]

    return CameraBYTETracker(_tracker_args(frame_rate), frame_rate=max(1, int(round(frame_rate))))


class ByteTracker:
    def __init__(self, detector_model=None, frame_rate: float = 30.0, camera_id: str = "unknown"):
        # `detector_model` is accepted for API compatibility; detection output is tracked directly.
        self.camera_id = camera_id
        self.frame_rate = frame_rate
        self._tracker = _build_camera_tracker(frame_rate)
        self.track_history: Dict[int, List] = {}

    def reset(self) -> None:
        self._tracker = _build_camera_tracker(self.frame_rate)
        self.track_history.clear()

    def track(self, frame: Optional[np.ndarray], detections: List[Dict]) -> List[Dict]:
        """Assign persistent track ids. Returns only confirmed tracks, each carrying its detection fields."""
        try:
            if detections:
                xyxy = np.array([[d["x1"], d["y1"], d["x2"], d["y2"]] for d in detections], dtype=np.float32)
                conf = np.array([d["confidence"] for d in detections], dtype=np.float32)
                cls = np.array([d["cls_id"] for d in detections], dtype=np.float32)
            else:
                xyxy = np.zeros((0, 4), dtype=np.float32)
                conf = np.zeros((0,), dtype=np.float32)
                cls = np.zeros((0,), dtype=np.float32)

            # Called on empty frames too, so lost tracks age out on schedule.
            rows = self._tracker.update(_TrackInput(xyxy, conf, cls), frame)

            tracked: List[Dict] = []
            for row in rows:
                index = int(row[7])
                track_id = int(row[4])
                if index < 0 or index >= len(detections):
                    continue
                source = detections[index]
                enriched = dict(source)
                enriched["track_id"] = track_id
                tracked.append(enriched)
                history = self.track_history.setdefault(track_id, [])
                history.append((source["cx"], source["cy"]))
                if len(history) > ml_config.trail_length:
                    del history[: len(history) - ml_config.trail_length]
            return tracked
        except Exception as exc:
            logger.error("[%s] tracking error: %s", self.camera_id, exc)
            return []
