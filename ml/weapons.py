"""Second-stage weapon detection: a small model that only looks at the people the main detector found.

COCO, which YOLOv8x is trained on, has no firearm class at all — knife and baseball bat are the only weapon-ish
labels, and on CCTV footage neither ever fires. This module runs a detector trained on weapons
(ml/training/train_weapons.py) over the region around each tracked person, which is both cheaper than a second
full-frame pass and far more accurate: a pistol that covers 20 px in the frame covers 200 px in the crop.

Its output is fed to the same weapon attribution the analyzer already applies to COCO detections, so a hit
raises the existing `weapon` alert with its +50 risk.

Without the trained weights the detector stays off and the pipeline behaves exactly as before.
"""
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from ml.config import ml_config

logger = logging.getLogger("sih26187.ml.weapons")

#: Classes the trained model knows; only these two are treated as weapons. The rest (phone, wallet, card)
#: exist so the model can tell a handheld phone from a pistol instead of guessing.
ALERTING_CLASSES = ("firearm", "knife")


class WeaponDetector:
    def __init__(self) -> None:
        self.model = None
        self.names: Dict[int, str] = {}
        self.enabled = False
        self.weights_sha256: Optional[str] = None
        self.inference_ms = 0.0
        self._lock = threading.Lock()
        self._load_failed = False

    @property
    def weights_path(self) -> Path:
        return Path(ml_config.weapon_model_path)

    def load(self) -> bool:
        """Load the weapon model. Returns False (once, with a clear reason) when it is not available."""
        if self.model is not None:
            return True
        if self._load_failed or not ml_config.weapon_model_enabled:
            return False
        weights = self.weights_path
        if not weights.is_file():
            logger.info("Weapon model not installed at %s — weapon detection stays off "
                        "(train it with: python -m ml.training.train_weapons)", weights)
            self._load_failed = True
            return False
        try:
            from ultralytics import YOLO

            from ml.detector import file_sha256

            self.weights_sha256 = file_sha256(str(weights))
            if ml_config.weapon_model_sha256 and ml_config.weapon_model_sha256.lower() != self.weights_sha256:
                logger.error("Weapon model hash mismatch: expected %s, got %s",
                             ml_config.weapon_model_sha256, self.weights_sha256)
                self._load_failed = True
                return False
            self.model = YOLO(str(weights))
            self.names = dict(self.model.names)
            self.enabled = True
            logger.info("Weapon model loaded: %s (classes: %s)", weights.name, ", ".join(self.names.values()))
            return True
        except Exception as exc:
            logger.error("Weapon model failed to load: %s", exc)
            self._load_failed = True
            return False

    def _crop(self, frame: np.ndarray, person: Dict) -> Optional[tuple]:
        """Box around a person, widened so a weapon held at arm's length is still inside it."""
        height, width = frame.shape[:2]
        pad = ml_config.weapon_crop_padding
        box_w = max(1.0, float(person["x2"] - person["x1"]))
        box_h = max(1.0, float(person["y2"] - person["y1"]))
        x1 = int(max(0, person["x1"] - box_w * pad))
        y1 = int(max(0, person["y1"] - box_h * pad * 0.5))
        x2 = int(min(width, person["x2"] + box_w * pad))
        y2 = int(min(height, person["y2"] + box_h * pad * 0.5))
        if x2 - x1 < 16 or y2 - y1 < 16:
            return None
        return x1, y1, x2, y2

    def detect(self, frame: Optional[np.ndarray], people: Sequence[Dict]) -> List[Dict]:
        """Weapon detections in frame coordinates, in the shape the analyzer expects from a COCO detection."""
        if frame is None or not len(people) or not self.load():
            return []

        crops, origins = [], []
        # Largest people first: they are nearest the camera, where a weapon is actually resolvable.
        ordered = sorted(people, key=lambda p: (p["x2"] - p["x1"]) * (p["y2"] - p["y1"]), reverse=True)
        for person in ordered[: max(1, ml_config.weapon_max_crops)]:
            box = self._crop(frame, person)
            if box is None:
                continue
            x1, y1, x2, y2 = box
            crops.append(frame[y1:y2, x1:x2])
            origins.append((x1, y1, person))
        if not crops:
            return []

        import time

        started = time.perf_counter()
        try:
            with self._lock:
                results = self.model.predict(
                    crops,
                    conf=ml_config.weapon_conf,
                    iou=0.45,
                    imgsz=ml_config.weapon_image_size,
                    device=ml_config.device,
                    half=ml_config.half_precision and ml_config.device == "cuda",
                    verbose=False,
                )
        except Exception as exc:
            logger.warning("Weapon inference failed: %s", exc)
            return []
        self.inference_ms = (time.perf_counter() - started) * 1000.0

        detections: List[Dict] = []
        for (offset_x, offset_y, person), result in zip(origins, results):
            for box in (result.boxes if result.boxes is not None else []):
                name = self.names.get(int(box.cls.item()), "")
                if name not in ALERTING_CLASSES:
                    continue
                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
                detection = {
                    "cls_name": name,
                    "confidence": float(box.conf.item()),
                    "x1": int(x1 + offset_x), "y1": int(y1 + offset_y),
                    "x2": int(x2 + offset_x), "y2": int(y2 + offset_y),
                    "cx": int((x1 + x2) / 2 + offset_x), "cy": int((y1 + y2) / 2 + offset_y),
                    "source": "weapon_model",
                    "near_track_id": person.get("track_id"),
                }
                detections.append(detection)
        if detections:
            logger.info("weapon model: %s", ", ".join(f"{d['cls_name']} {d['confidence']:.2f}" for d in detections))
        return detections


weapon_detector = WeaponDetector()
