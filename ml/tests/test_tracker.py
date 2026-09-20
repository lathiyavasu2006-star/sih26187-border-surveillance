"""ByteTrack wrapper: persistent ids, occlusion recovery, per-camera isolation."""
import pytest

from ml.tests.conftest import shifted_frames
from ml.tracker import ByteTracker


def box(x, y, w=60, h=160, conf=0.9, cls_id=0, cls_name="person"):
    return {"x1": x, "y1": y, "x2": x + w, "y2": y + h, "cx": x + w // 2, "cy": y + h // 2,
            "cls_id": cls_id, "cls_name": cls_name, "confidence": conf, "track_id": -1}


def run(tracker, frames):
    return [tracker.track(None, detections) for detections in frames]


def test_ids_persist_while_objects_move():
    tracker = ByteTracker(frame_rate=30, camera_id="CAM-T-1")
    frames = [[box(100 + 4 * i, 200), box(500 - 3 * i, 220)] for i in range(40)]
    outputs = run(tracker, frames)

    confirmed = [o for o in outputs if len(o) == 2]
    assert len(confirmed) >= 35
    ids_first = sorted(d["track_id"] for d in confirmed[0])
    ids_last = sorted(d["track_id"] for d in confirmed[-1])
    assert ids_first == ids_last, "track ids must not change while objects stay visible"
    assert len(set(ids_first)) == 2 and all(i >= 1 for i in ids_first)

    left = next(d for d in confirmed[-1] if d["x1"] > 200)
    assert left["cx"] == left["x1"] + 30  # detection fields are carried through


def test_track_survives_short_occlusion():
    tracker = ByteTracker(frame_rate=30, camera_id="CAM-T-2")
    before = run(tracker, [[box(100 + 5 * i, 200)] for i in range(15)])
    track_id = before[-1][0]["track_id"]

    assert run(tracker, [[] for _ in range(10)])[-1] == []          # hidden for 10 frames
    # Still walking at 5 px/frame behind the obstacle, so it reappears where the Kalman filter predicts.
    after = run(tracker, [[box(100 + 5 * (25 + i), 200)] for i in range(5)])
    assert after[-1][0]["track_id"] == track_id, "ByteTrack must re-associate within the track buffer"


def test_track_is_forgotten_after_buffer_expires():
    tracker = ByteTracker(frame_rate=30, camera_id="CAM-T-3")
    first = run(tracker, [[box(100, 200)] for _ in range(10)])[-1][0]["track_id"]
    run(tracker, [[] for _ in range(80)])                            # far beyond track_buffer=30
    later = run(tracker, [[box(600, 200)] for _ in range(10)])[-1][0]["track_id"]
    assert later != first


def test_new_tracker_does_not_reset_other_cameras_ids():
    """Ultralytics resets a global id counter per tracker; ours are independent per camera."""
    camera_a = ByteTracker(frame_rate=30, camera_id="CAM-A")
    a_ids_before = {d["track_id"] for d in run(camera_a, [[box(50, 50), box(300, 50)] for _ in range(10)])[-1]}

    camera_b = ByteTracker(frame_rate=30, camera_id="CAM-B")                  # created mid-stream
    run(camera_b, [[box(50, 50)] for _ in range(10)])

    # Camera A gains a third object: its new id must not collide with the two live ones.
    a_after = run(camera_a, [[box(50, 50), box(300, 50), box(600, 300)] for _ in range(10)])[-1]
    a_ids_after = [d["track_id"] for d in a_after]
    assert len(a_ids_after) == 3
    assert len(set(a_ids_after)) == 3, f"duplicate track ids on one camera: {a_ids_after}"
    assert a_ids_before <= set(a_ids_after)


def test_low_confidence_boxes_keep_existing_tracks_alive():
    tracker = ByteTracker(frame_rate=30, camera_id="CAM-T-4")
    track_id = run(tracker, [[box(100 + 2 * i, 100, conf=0.9)] for i in range(10)])[-1][0]["track_id"]
    # Confidence drops (e.g. partial occlusion) below the detection threshold but above the low threshold.
    weak = run(tracker, [[box(120 + 2 * i, 100, conf=0.25)] for i in range(5)])
    assert weak[-1] and weak[-1][0]["track_id"] == track_id


def test_reset_starts_fresh():
    tracker = ByteTracker(frame_rate=30, camera_id="CAM-T-5")
    run(tracker, [[box(100, 100)] for _ in range(5)])
    tracker.reset()
    assert tracker.track_history == {}
    assert run(tracker, [[box(100, 100)] for _ in range(5)])[-1][0]["track_id"] == 1


def test_real_detections_keep_ids_across_frames(loaded_detector, bus_image):
    from ml.config import ml_config

    tracker = ByteTracker(frame_rate=15, camera_id="CAM-REAL")
    frames = shifted_frames(bus_image, 20, step_px=4)
    history = []
    for frame in frames:
        detections = loaded_detector.detect(frame, conf=ml_config.tracker_input_conf)
        history.append({d["track_id"]: d for d in tracker.track(frame, detections) if d["cls_name"] == "person"})

    early_ids = set(history[3])
    late_ids = set(history[-1])
    assert len(early_ids) >= 3
    assert len(early_ids & late_ids) >= max(2, len(early_ids) - 1), (early_ids, late_ids)
