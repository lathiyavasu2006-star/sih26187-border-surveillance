"""Second-stage weapon detection: crop placement, class filtering and the off-by-default behaviour."""
import numpy as np
import pytest

from ml.config import ml_config
from ml.weapons import WeaponDetector


class _Scalar(float):
    def item(self):
        return float(self)


class Box:
    """Mimics one ultralytics box closely enough for the mapping code."""

    def __init__(self, xyxy, cls, conf):
        self.xyxy = [np.array(xyxy, dtype=np.float32)]
        self.cls = _Scalar(cls)
        self.conf = _Scalar(conf)


class Result:
    def __init__(self, boxes):
        self.boxes = boxes


class FakeModel:
    """Returns the same boxes for every crop, and records the crops it was asked about."""

    names = {0: "firearm", 1: "knife", 2: "phone", 3: "wallet", 4: "card"}

    def __init__(self, boxes_per_crop):
        self.boxes_per_crop = boxes_per_crop
        self.crops = []
        self.kwargs = {}

    def predict(self, crops, **kwargs):
        self.crops = list(crops)
        self.kwargs = kwargs
        return [Result(list(self.boxes_per_crop)) for _ in crops]


def person(x1, y1, x2, y2, track_id=1):
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
            "cls_name": "person", "track_id": track_id, "confidence": 0.9}


def detector_with(model) -> WeaponDetector:
    detector = WeaponDetector()
    detector.model = model
    detector.names = dict(model.names)
    detector.enabled = True
    return detector


def test_boxes_come_back_in_frame_coordinates():
    frame = np.zeros((720, 1280, 3), np.uint8)
    # The crop starts at the padded person box, so a box at (10, 20) inside it is offset by that origin.
    model = FakeModel([Box([10, 20, 40, 35], cls=0, conf=0.81)])
    detector = detector_with(model)

    found = detector.detect(frame, [person(500, 300, 560, 460)])
    assert len(found) == 1
    weapon = found[0]
    x1, _y1, _x2, _y2 = 500 - int(60 * ml_config.weapon_crop_padding), 0, 0, 0
    assert weapon["cls_name"] == "firearm"
    assert weapon["confidence"] == pytest.approx(0.81, abs=1e-3)
    assert weapon["x1"] == x1 + 10
    assert weapon["cx"] == x1 + 25
    assert weapon["near_track_id"] == 1
    assert weapon["source"] == "weapon_model"
    # The crop is wider than the person and stays inside the frame.
    height, width = model.crops[0].shape[:2]
    assert width > 60 and height >= 160


def test_everyday_objects_are_not_weapons():
    frame = np.zeros((480, 640, 3), np.uint8)
    detector = detector_with(FakeModel([Box([5, 5, 25, 25], cls=2, conf=0.9), Box([5, 5, 25, 25], cls=4, conf=0.7)]))
    assert detector.detect(frame, [person(100, 100, 160, 300)]) == []


def test_only_the_nearest_people_are_checked(monkeypatch):
    monkeypatch.setattr(ml_config, "weapon_max_crops", 2)
    frame = np.zeros((720, 1280, 3), np.uint8)
    model = FakeModel([])
    detector = detector_with(model)
    people = [person(0, 0, 40, 80, 1), person(100, 100, 300, 500, 2), person(400, 100, 700, 600, 3)]

    detector.detect(frame, people)
    assert len(model.crops) == 2
    # Biggest first: the two largest crops, not the first two in the list.
    assert model.crops[0].size > model.crops[1].size
    assert model.kwargs["conf"] == ml_config.weapon_conf
    assert model.kwargs["imgsz"] == ml_config.weapon_image_size


def test_tiny_people_are_skipped():
    frame = np.zeros((720, 1280, 3), np.uint8)
    model = FakeModel([])
    detector = detector_with(model)
    detector.detect(frame, [person(10, 10, 18, 20)])
    assert model.crops == []


def test_missing_weights_disable_the_stage(monkeypatch, tmp_path):
    monkeypatch.setattr(ml_config, "weapon_model_path", str(tmp_path / "not-trained-yet.pt"))
    detector = WeaponDetector()
    assert detector.load() is False
    assert detector.detect(np.zeros((480, 640, 3), np.uint8), [person(10, 10, 100, 300)]) == []
    assert detector.enabled is False


def test_inference_failure_does_not_break_the_frame():
    class Exploding(FakeModel):
        def predict(self, crops, **kwargs):
            raise RuntimeError("CUDA out of memory")

    detector = detector_with(Exploding([]))
    assert detector.detect(np.zeros((720, 1280, 3), np.uint8), [person(100, 100, 300, 500)]) == []
