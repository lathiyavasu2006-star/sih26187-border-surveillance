"""YOLOv8x object detector on CUDA (FP16), shared by every camera pipeline.

Security / reliability hardening of the Ultralytics defaults:
* runtime `pip install` (AUTOINSTALL) is disabled — a production process must never modify itself;
* anonymous usage analytics (`sync`) are turned off — no telemetry leaves a border surveillance system;
* the weights file is hashed at load time and can be pinned with ML_MODEL_SHA256.
"""
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

os.environ.setdefault("YOLO_AUTOINSTALL", "False")
os.environ.setdefault("YOLO_VERBOSE", "False")

import numpy as np  # noqa: E402
import torch  # noqa: E402

from ml.config import ml_config  # noqa: E402

logger = logging.getLogger("sih26187.ml.detector")


def _disable_ultralytics_telemetry() -> None:
    from ultralytics import settings

    if settings.get("sync", False):
        settings.update({"sync": False})


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clamp_box(x1: float, y1: float, x2: float, y2: float, width: int, height: int):
    x1 = int(max(0, min(x1, width - 1)))
    y1 = int(max(0, min(y1, height - 1)))
    x2 = int(max(x1, min(x2, width - 1)))
    y2 = int(max(y1, min(y2, height - 1)))
    return x1, y1, x2, y2


class YOLOv8Detector:
    def __init__(self):
        self.device = ml_config.device
        self.conf_threshold = ml_config.conf_threshold
        self.iou_threshold = ml_config.iou_threshold
        self.target_classes = list(ml_config.target_classes)
        self.class_names: Dict[int, str] = dict(ml_config.class_names)
        self.model = None
        self.half = False
        self.backend = "none"  # "pytorch" or "tensorrt"
        self.engine_sha256: Optional[str] = None
        self.model_sha256: Optional[str] = None
        self._lock = threading.Lock()  # one GPU model shared by all camera pipelines
        self.inference_ms = 0.0

    @property
    def loaded(self) -> bool:
        return self.model is not None

    def _ensure_weights(self) -> str:
        """Download the official weights into the models folder if they are not there yet."""
        from ultralytics.utils.downloads import attempt_download_asset

        target = Path(ml_config.model_path)
        if not target.exists():
            logger.warning("Model weights not found at %s — downloading %s from Ultralytics releases",
                           target, target.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            attempt_download_asset(str(target))
        if not target.exists() or target.stat().st_size < 1_000_000:
            raise FileNotFoundError(f"Model weights unavailable at {target}")
        return str(target)

    def load(self) -> bool:
        try:
            if self.device != "cuda" and os.environ.get("ML_ALLOW_CPU", "").lower() not in {"1", "true"}:
                logger.error("CUDA is not available; refusing to run YOLOv8x on CPU (set ML_ALLOW_CPU=true to override)")
                return False
            _disable_ultralytics_telemetry()
            from ultralytics import YOLO

            weights = self._ensure_weights()
            self.model_sha256 = file_sha256(weights)
            if ml_config.model_sha256 and ml_config.model_sha256.lower() != self.model_sha256:
                logger.error("Model hash mismatch: expected %s, got %s", ml_config.model_sha256, self.model_sha256)
                return False

            engine = self._verified_engine() if self.device == "cuda" else None
            if engine:
                self.model = YOLO(engine, task="detect")
                self.half = True
                self.backend = "tensorrt"
                logger.info("YOLOv8x TensorRT FP16 engine loaded (engine sha256=%s, built from %s)",
                            self.engine_sha256, self.model_sha256)
            else:
                self.model = YOLO(weights)
                self.model.to(self.device)
                self.half = self.device == "cuda" and ml_config.half_precision
                self.backend = "pytorch"
                logger.info("YOLOv8x loaded on %s (fp16=%s) sha256=%s", self.device, self.half, self.model_sha256)
            if self.device == "cuda":
                props = torch.cuda.get_device_properties(0)
                logger.info("GPU: %s | VRAM: %.1f GB", torch.cuda.get_device_name(0), props.total_memory / 1e9)

            dummy = np.zeros((ml_config.image_size, ml_config.image_size, 3), dtype=np.uint8)
            for _ in range(2):  # first call builds CUDA kernels; second measures steady state
                self.detect(dummy)
            logger.info("Model warmup complete (%.1f ms/frame)", self.inference_ms)
            return True
        except Exception as exc:
            logger.exception("Model load failed: %s", exc)
            self.model = None
            return False

    def _verified_engine(self) -> Optional[str]:
        """Engine path if it exists and provably matches these weights, this GPU and this TensorRT."""
        if not ml_config.use_tensorrt:
            return None
        engine = Path(ml_config.engine_path)
        sidecar = Path(str(engine) + ".json")
        if not engine.is_file() or not sidecar.is_file():
            return None
        try:
            import tensorrt

            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            problems = []
            if meta.get("source_sha256") != self.model_sha256:
                problems.append("built from different weights")
            if meta.get("gpu") != torch.cuda.get_device_name(0):
                problems.append(f"built on {meta.get('gpu')}")
            if meta.get("tensorrt_version") != tensorrt.__version__:
                problems.append(f"built with TensorRT {meta.get('tensorrt_version')}")
            if int(meta.get("image_size", -1)) != ml_config.image_size:
                problems.append(f"built for imgsz {meta.get('image_size')}")
            engine_hash = file_sha256(str(engine))
            if meta.get("engine_sha256") != engine_hash:
                problems.append("engine file hash does not match its sidecar")
            if problems:
                logger.warning("Ignoring TensorRT engine (%s); rebuild with `python -m ml.build_engine`", "; ".join(problems))
                return None
            self.engine_sha256 = engine_hash
            return str(engine)
        except ImportError:
            logger.warning("TensorRT engine present but tensorrt is not installed; using PyTorch")
            return None
        except (OSError, ValueError) as exc:
            logger.warning("Ignoring TensorRT engine: %s", exc)
            return None

    def detect(self, frame: np.ndarray, conf: Optional[float] = None, classes: Optional[List[int]] = None) -> List[Dict]:
        """Run YOLO on one BGR frame. Thread-safe; blocking (call through asyncio.to_thread in async code)."""
        if self.model is None or frame is None:
            return []
        height, width = frame.shape[:2]
        try:
            started = time.perf_counter()
            with self._lock:
                results = self.model.predict(
                    frame,
                    conf=self.conf_threshold if conf is None else conf,
                    iou=self.iou_threshold,
                    classes=classes if classes is not None else self.target_classes,
                    imgsz=ml_config.image_size,
                    device=self.device,
                    half=self.half if self.backend == "pytorch" else False,
                    verbose=False,
                )
            self.inference_ms = (time.perf_counter() - started) * 1000.0

            detections: List[Dict] = []
            for result in results:
                boxes = result.boxes
                if boxes is None or len(boxes) == 0:
                    continue
                data = boxes.data.float().cpu().numpy()  # one GPU->CPU transfer: x1 y1 x2 y2 conf cls
                xyxy, confs, cls_ids = data[:, :4], data[:, 4], data[:, 5].astype(int)
                for (bx1, by1, bx2, by2), score, cls_id in zip(xyxy, confs, cls_ids):
                    if cls_id not in self.class_names:
                        continue
                    x1, y1, x2, y2 = clamp_box(bx1, by1, bx2, by2, width, height)
                    if (x2 - x1) * (y2 - y1) < ml_config.min_box_area:
                        continue
                    detections.append({
                        "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                        "cx": (x1 + x2) // 2,
                        "cy": (y1 + y2) // 2,
                        "cls_id": int(cls_id),
                        "cls_name": self.class_names[int(cls_id)],
                        "confidence": round(float(score), 3),
                        "track_id": -1,  # filled by the tracker
                    })
            return detections
        except Exception as exc:
            logger.error("Detection error: %s", exc)
            return []


detector = YOLOv8Detector()
