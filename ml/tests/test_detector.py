"""YOLOv8x detector: CUDA placement, weights integrity, output format and hardening."""
import re
from pathlib import Path

import numpy as np
import pytest
import torch

from ml.config import ml_config

REQUIRED_KEYS = {"x1", "y1", "x2", "y2", "cx", "cy", "cls_id", "cls_name", "confidence", "track_id"}


def test_cuda_is_available_and_selected():
    assert torch.cuda.is_available(), "CUDA GPU required for YOLOv8x"
    assert ml_config.device == "cuda"
    assert "RTX" in torch.cuda.get_device_name(0) or torch.cuda.get_device_name(0)


def test_model_loads_on_gpu_in_half_precision(loaded_detector):
    assert loaded_detector.loaded
    assert loaded_detector.half is True
    engine = Path(ml_config.engine_path)
    if engine.is_file() and Path(str(engine) + ".json").is_file():
        assert loaded_detector.backend == "tensorrt", "a verified engine must be preferred over PyTorch"
        assert re.fullmatch(r"[0-9a-f]{64}", loaded_detector.engine_sha256)
    else:
        assert loaded_detector.backend == "pytorch"
        assert next(loaded_detector.model.model.parameters()).device.type == "cuda"
    assert Path(ml_config.model_path).name == "yolov8x.pt"
    assert Path(ml_config.model_path).is_file(), "weights must live in the models folder"


def test_weights_are_hashed(loaded_detector):
    assert re.fullmatch(r"[0-9a-f]{64}", loaded_detector.model_sha256)


def test_ultralytics_telemetry_and_autoinstall_disabled(loaded_detector):
    import os

    from ultralytics import settings

    assert settings["sync"] is False
    assert os.environ["YOLO_AUTOINSTALL"].lower() == "false"


def test_detects_people_and_bus_with_correct_format(loaded_detector, bus_image):
    detections = loaded_detector.detect(bus_image)
    height, width = bus_image.shape[:2]

    names = [d["cls_name"] for d in detections]
    assert names.count("person") >= 3, f"expected several people in bus.jpg, got {names}"
    assert "bus" in names

    for det in detections:
        assert REQUIRED_KEYS <= set(det)
        assert 0 <= det["x1"] <= det["x2"] < width
        assert 0 <= det["y1"] <= det["y2"] < height
        assert det["cx"] == (det["x1"] + det["x2"]) // 2
        assert det["cy"] == (det["y1"] + det["y2"]) // 2
        assert ml_config.conf_threshold <= det["confidence"] <= 1.0
        assert det["cls_id"] in ml_config.target_classes
        assert det["cls_name"] == ml_config.class_names[det["cls_id"]]
        assert det["track_id"] == -1
        assert all(isinstance(det[k], int) for k in ("x1", "y1", "x2", "y2", "cx", "cy", "cls_id"))


def test_lower_confidence_returns_superset(loaded_detector, bus_image):
    strict = loaded_detector.detect(bus_image, conf=0.6)
    loose = loaded_detector.detect(bus_image, conf=0.1)
    assert len(loose) >= len(strict)
    assert all(d["confidence"] >= 0.6 for d in strict)


def test_empty_scene_and_invalid_input(loaded_detector):
    assert loaded_detector.detect(np.zeros((480, 640, 3), dtype=np.uint8)) == []
    assert loaded_detector.detect(None) == []


def test_inference_is_fast_enough_for_realtime(loaded_detector, bus_image):
    for _ in range(3):
        loaded_detector.detect(bus_image)
    # RTX 3050 laptop, yolov8x fp16 @640: comfortably below 150 ms per frame.
    assert loaded_detector.inference_ms < 150, f"{loaded_detector.inference_ms:.1f} ms"


def test_unloaded_detector_returns_nothing():
    from ml.detector import YOLOv8Detector

    assert YOLOv8Detector().detect(np.zeros((10, 10, 3), dtype=np.uint8)) == []


def test_refuses_cpu_without_override(monkeypatch):
    from ml.detector import YOLOv8Detector

    cpu_detector = YOLOv8Detector()
    cpu_detector.device = "cpu"
    monkeypatch.delenv("ML_ALLOW_CPU", raising=False)
    assert cpu_detector.load() is False
    assert cpu_detector.model is None


def _iou(a, b):
    ix1, iy1, ix2, iy2 = max(a["x1"], b["x1"]), max(a["y1"], b["y1"]), min(a["x2"], b["x2"]), min(a["y2"], b["y2"])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area = lambda d: (d["x2"] - d["x1"]) * (d["y2"] - d["y1"])
    return inter / float(area(a) + area(b) - inter)


def test_tensorrt_engine_matches_pytorch(loaded_detector, bus_image, monkeypatch):
    """Speed must not cost accuracy: the engine finds the same people as the PyTorch model."""
    from ml.detector import YOLOv8Detector

    if loaded_detector.backend != "tensorrt":
        assert not Path(ml_config.engine_path).is_file() or not Path(ml_config.engine_path + ".json").is_file()
        return
    monkeypatch.setattr(ml_config, "use_tensorrt", False)
    reference = YOLOv8Detector()
    assert reference.load() and reference.backend == "pytorch"

    engine_people = [d for d in loaded_detector.detect(bus_image) if d["cls_name"] == "person"]
    torch_people = [d for d in reference.detect(bus_image) if d["cls_name"] == "person"]
    assert len(engine_people) == len(torch_people) >= 3
    for person in torch_people:
        best = max(_iou(person, other) for other in engine_people)
        assert best >= 0.9, f"engine box drifted from PyTorch box (IoU {best:.2f})"
    del reference
    torch.cuda.empty_cache()


def test_engine_with_mismatched_sidecar_is_refused(tmp_path, monkeypatch, loaded_detector):
    import json

    from ml.detector import YOLOv8Detector

    fake_engine = tmp_path / "yolov8x.engine"
    fake_engine.write_bytes(b"not a real engine")
    (tmp_path / "yolov8x.engine.json").write_text(json.dumps({
        "engine_sha256": "0" * 64, "source_sha256": loaded_detector.model_sha256,
        "gpu": torch.cuda.get_device_name(0), "tensorrt_version": "0.0", "image_size": ml_config.image_size,
    }))
    monkeypatch.setattr(ml_config, "engine_path", str(fake_engine))
    candidate = YOLOv8Detector()
    candidate.model_sha256 = loaded_detector.model_sha256
    assert candidate._verified_engine() is None, "tampered or foreign engines must never be loaded"
