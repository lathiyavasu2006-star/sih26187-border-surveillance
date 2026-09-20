"""ML pipeline configuration.

Values come from E:/sih26187/.env (shared with the backend) with ML_* overrides, so credentials and
thresholds are never hard-coded. Process environment variables win over the .env file.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import torch
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = PROJECT_ROOT / ".env"

_FILE_VALUES = {k: v for k, v in dotenv_values(ENV_FILE).items() if v is not None} if ENV_FILE.exists() else {}


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        value = _FILE_VALUES.get(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return str(_env(name, str(default))).strip().lower() in {"1", "true", "yes", "on"}


# COCO class ids (the YOLOv8 pretrained label set).
PERSON_CLASSES = {0: "person"}
VEHICLE_CLASSES = {1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
ANIMAL_CLASSES = {15: "cat", 16: "dog", 17: "horse", 18: "sheep", 19: "cow", 20: "elephant", 21: "bear"}
# COCO has no firearm class. Knives and bats are detected and attributed to the nearest person;
# firearm detection needs a custom-trained model and is out of scope for the pretrained weights.
WEAPON_CLASSES = {34: "baseball bat", 43: "knife"}


@dataclass
class MLConfig:
    # Paths
    project_root: str = str(PROJECT_ROOT).replace("\\", "/")
    models_path: str = field(default_factory=lambda: _env("MODELS_PATH", "E:/sih26187/models/"))
    evidence_path: str = field(default_factory=lambda: _env("EVIDENCE_PATH", "E:/sih26187/evidence/"))
    snapshots_path: str = field(default_factory=lambda: _env("SNAPSHOTS_PATH", "E:/sih26187/evidence/snapshots/"))
    clips_path: str = field(default_factory=lambda: _env("CLIPS_PATH", "E:/sih26187/evidence/clips/"))
    logs_path: str = field(default_factory=lambda: _env("LOGS_PATH", "E:/sih26187/logs/"))

    # Detection
    model_name: str = field(default_factory=lambda: _env("ML_MODEL_NAME", "yolov8x.pt"))
    model_path: str = ""
    # TensorRT FP16 engine built by `python -m ml.build_engine`; used only when its sidecar matches.
    use_tensorrt: bool = field(default_factory=lambda: _env_bool("ML_USE_TENSORRT", True))
    engine_path: str = ""
    model_sha256: Optional[str] = field(default_factory=lambda: _env("ML_MODEL_SHA256"))
    conf_threshold: float = field(default_factory=lambda: _env_float("CONF_THRESHOLD", 0.4))
    iou_threshold: float = field(default_factory=lambda: _env_float("ML_IOU_THRESHOLD", 0.45))
    tracker_input_conf: float = field(default_factory=lambda: _env_float("ML_TRACKER_INPUT_CONF", 0.1))
    image_size: int = field(default_factory=lambda: _env_int("ML_IMAGE_SIZE", 640))
    #: Offline video analysis runs larger and on every frame: it has no real-time budget to respect, and small
    #: or fast-moving people are the whole point of re-watching a recording.
    analysis_image_size: int = field(default_factory=lambda: _env_int("ML_ANALYSIS_IMAGE_SIZE", 960))
    #: 0 = choose per video: every frame for short clips, stepping up so a long recording still finishes
    #: within analysis_max_frames detections.
    analysis_frame_step: int = field(default_factory=lambda: _env_int("ML_ANALYSIS_FRAME_STEP", 0))
    analysis_max_frames: int = field(default_factory=lambda: _env_int("ML_ANALYSIS_MAX_FRAMES", 4000))
    #: The TensorRT engine is built for image_size; analysis uses PyTorch so it can run at a different size.
    analysis_use_tensorrt: bool = field(default_factory=lambda: _env_bool("ML_ANALYSIS_USE_TENSORRT", False))
    device: str = "cuda" if torch.cuda.is_available() else "cpu"
    half_precision: bool = field(default_factory=lambda: _env_bool("ML_HALF_PRECISION", True))
    detect_animals: bool = field(default_factory=lambda: _env_bool("ML_DETECT_ANIMALS", True))
    detect_weapons: bool = field(default_factory=lambda: _env_bool("ML_DETECT_WEAPONS", True))
    target_classes: List[int] = field(default_factory=list)
    class_names: Dict[int, str] = field(default_factory=dict)

    # Tracking
    # ~3 s at 30 fps: short occlusions and missed detections keep their track instead of spawning a new id.
    track_buffer: int = field(default_factory=lambda: _env_int("ML_TRACK_BUFFER", 90))
    match_threshold: float = field(default_factory=lambda: _env_float("ML_MATCH_THRESHOLD", 0.8))
    #: Confidence a detection needs to START a track. People far from the camera, partly hidden or blurred by
    #: motion score 0.25-0.35, so the old 0.45 dropped them entirely; ByteTrack then keeps them with weaker
    #: evidence once the track exists.
    track_new_conf: float = field(default_factory=lambda: _env_float("ML_TRACK_NEW_CONF", 0.30))
    min_box_area: int = field(default_factory=lambda: _env_int("ML_MIN_BOX_AREA", 10))
    trail_length: int = field(default_factory=lambda: _env_int("TRAIL_LENGTH", 50))
    lost_track_seconds: float = field(default_factory=lambda: _env_float("ML_LOST_TRACK_SECONDS", 1.5))
    reappear_radius_px: int = field(default_factory=lambda: _env_int("ML_REAPPEAR_RADIUS_PX", 80))
    reappear_window_seconds: float = field(default_factory=lambda: _env_float("ML_REAPPEAR_WINDOW_SECONDS", 120.0))
    # Appearance re-identification (ml/identity.py): a returning person keeps the same id.
    reid_enabled: bool = field(default_factory=lambda: _env_bool("ML_REID_ENABLED", True))
    reid_window_seconds: float = field(default_factory=lambda: _env_float("ML_REID_WINDOW_SECONDS", 600.0))
    reid_max_distance: float = field(default_factory=lambda: _env_float("ML_REID_MAX_DISTANCE", 0.45))

    # Night / adverse weather
    night_brightness_threshold: int = field(default_factory=lambda: _env_int("NIGHT_BRIGHTNESS_THRESHOLD", 80))
    night_start_hour: int = field(default_factory=lambda: _env_int("NIGHT_START_HOUR", 22))
    night_end_hour: int = field(default_factory=lambda: _env_int("NIGHT_END_HOUR", 5))
    night_risk_multiplier: float = field(default_factory=lambda: _env_float("NIGHT_RISK_MULTIPLIER", 1.5))
    fog_contrast_threshold: float = field(default_factory=lambda: _env_float("ML_FOG_CONTRAST_THRESHOLD", 20.0))
    fog_brightness_threshold: float = field(default_factory=lambda: _env_float("ML_FOG_BRIGHTNESS_THRESHOLD", 100.0))

    # Loitering and alerts
    default_loiter_threshold: int = field(default_factory=lambda: _env_int("LOITER_THRESHOLD_SECONDS", 30))
    alert_cooldown_seconds: int = field(default_factory=lambda: _env_int("ALERT_COOLDOWN_SECONDS", 15))
    running_speed_heights_per_second: float = field(default_factory=lambda: _env_float("ML_RUNNING_SPEED", 1.8))

    # Risk scoring
    base_risk_score: int = 10
    zone_risk_bonus: Dict[str, int] = field(default_factory=dict)
    risk_levels: Dict[str, Tuple[int, int]] = field(default_factory=dict)
    alert_risk_levels: Tuple[str, ...] = ("suspicious", "high_risk", "critical")

    # Evidence
    frame_buffer_seconds: int = field(default_factory=lambda: _env_int("ML_PRE_ALERT_SECONDS", 15))
    frame_buffer_size: int = 450  # 15 s at 30 fps; buffered frames are JPEG-compressed in memory
    clip_duration_after_alert: int = field(default_factory=lambda: _env_int("ML_POST_ALERT_SECONDS", 15))
    buffer_jpeg_quality: int = 85
    snapshot_jpeg_quality: int = 95
    record_clips: bool = field(default_factory=lambda: _env_bool("ML_RECORD_CLIPS", True))

    # Backend
    backend_url: str = field(default_factory=lambda: _env("ML_BACKEND_URL", "http://localhost:8000"))
    backend_ws_url: str = field(default_factory=lambda: _env("ML_BACKEND_WS_URL", "ws://localhost:8000"))
    admin_username: str = field(default_factory=lambda: _env("ML_USERNAME", _env("ADMIN_USERNAME", "admin")))
    admin_password: str = field(default_factory=lambda: _env("ML_PASSWORD", _env("ADMIN_PASSWORD", "")))
    zone_cache_seconds: float = 30.0
    http_timeout_seconds: float = 15.0

    # Streaming to the backend: frames for the live view, detections persisted less often.
    ws_send_fps: float = field(default_factory=lambda: _env_float("ML_WS_SEND_FPS", 10.0))
    detection_publish_hz: float = field(default_factory=lambda: _env_float("ML_DETECTION_PUBLISH_HZ", 2.0))
    ws_jpeg_quality: int = 75
    ws_max_frame_width: int = field(default_factory=lambda: _env_int("ML_WS_MAX_FRAME_WIDTH", 1280))

    # Performance
    target_fps: int = 30
    # Webcam exposure in log2 seconds (-6 = 1/64 s). None keeps auto exposure. Auto exposure lets a webcam
    # halve its frame rate in dim light (30 -> 15 fps); a fixed short exposure keeps 30 fps but a darker image.
    camera_exposure: Optional[float] = field(default_factory=lambda: (
        float(_env("ML_CAMERA_EXPOSURE")) if _env("ML_CAMERA_EXPOSURE") not in (None, "", "auto") else None))
    max_cameras: int = 16

    def __post_init__(self):
        self.model_path = str(Path(self.models_path) / self.model_name).replace("\\", "/")
        self.engine_path = str(Path(self.model_path).with_suffix(".engine")).replace("\\", "/")
        self.class_names = {**PERSON_CLASSES, **{k: v for k, v in VEHICLE_CLASSES.items() if k != 1}}
        if self.detect_animals:
            self.class_names.update(ANIMAL_CLASSES)
        if self.detect_weapons:
            self.class_names.update(WEAPON_CLASSES)
        self.target_classes = sorted(self.class_names)
        self.zone_risk_bonus = {
            "public": 0,
            "buffer": 10,
            "sensitive": 30,
            "restricted": 50,
            "no_mans_land": 100,
        }
        self.risk_levels = {
            "normal": (0, 20),
            "low": (21, 40),
            "suspicious": (41, 60),
            "high_risk": (61, 80),
            "critical": (81, 100),
        }
        self.ensure_directories()

    def ensure_directories(self) -> None:
        for path in (self.snapshots_path, self.clips_path, self.models_path, self.logs_path):
            Path(path).mkdir(parents=True, exist_ok=True)

    # ---- classification helpers

    def is_person(self, cls_name: str) -> bool:
        return cls_name == "person"

    def is_vehicle(self, cls_name: str) -> bool:
        return cls_name in VEHICLE_CLASSES.values()

    def is_animal(self, cls_name: str) -> bool:
        return cls_name in ANIMAL_CLASSES.values()

    def is_weapon(self, cls_name: str) -> bool:
        return cls_name in WEAPON_CLASSES.values()

    def risk_level_for(self, score: int) -> str:
        score = max(0, min(100, int(score)))
        for level, (low, high) in self.risk_levels.items():
            if low <= score <= high:
                return level
        return "critical"

    def is_night_hour(self, hour: int, start: Optional[int] = None, end: Optional[int] = None) -> bool:
        start = self.night_start_hour if start is None else int(start)
        end = self.night_end_hour if end is None else int(end)
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end


ml_config = MLConfig()
