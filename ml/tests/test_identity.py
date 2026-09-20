"""Stable identities: a person who leaves and returns keeps the same id; different people do not merge."""
import numpy as np

from ml.identity import IdentityResolver, appearance_descriptor, appearance_distance


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def person(frame, x, y, top, bottom, w=60, h=150):
    frame[y:y + h // 2, x:x + w] = top
    frame[y + h // 2:y + h, x:x + w] = bottom
    return {"x1": x, "y1": y, "x2": x + w, "y2": y + h, "cx": x + w // 2, "cy": y + h // 2, "cls_name": "person"}


def blank():
    return np.full((480, 640, 3), 90, np.uint8)


RED, BLUE, GREEN, YELLOW = (30, 30, 200), (200, 60, 30), (40, 180, 40), (40, 220, 230)


def detection(raw_id, box):
    return dict(box, track_id=raw_id)


def test_descriptor_separates_clothing_colours():
    a, b = blank(), blank()
    box_a = person(a, 100, 100, RED, BLUE)
    box_b = person(b, 100, 100, GREEN, YELLOW)
    same = appearance_descriptor(a, (box_a["x1"], box_a["y1"], box_a["x2"], box_a["y2"]))
    other = appearance_descriptor(b, (box_b["x1"], box_b["y1"], box_b["x2"], box_b["y2"]))
    assert appearance_distance(same, same) < 0.01
    assert appearance_distance(same, other) > 0.8
    assert appearance_descriptor(a, (0, 0, 3, 3)) is None, "tiny crops are not described"


def test_returning_person_keeps_the_same_id():
    clock = Clock()
    resolver = IdentityResolver("CAM-T", window_seconds=600, max_distance=0.45, clock=clock)
    frame = blank()
    first = resolver.resolve(frame, [detection(1, person(frame, 100, 100, RED, BLUE))])
    assert first[0]["track_id"] == 1 and first[0]["raw_track_id"] == 1

    clock.now = 30  # left the frame for 30 s; ByteTrack has forgotten raw id 1
    frame = blank()
    back = resolver.resolve(frame, [detection(7, person(frame, 400, 200, RED, BLUE))])
    assert back[0]["track_id"] == 1, "same clothes within the window → same identity"
    assert back[0]["raw_track_id"] == 7
    assert resolver.reidentified == 1 and resolver.identity_count == 1


def test_different_person_gets_a_new_id():
    clock = Clock()
    resolver = IdentityResolver("CAM-T", clock=clock)
    frame = blank()
    resolver.resolve(frame, [detection(1, person(frame, 100, 100, RED, BLUE))])
    clock.now = 10
    frame = blank()
    other = resolver.resolve(frame, [detection(2, person(frame, 100, 100, GREEN, YELLOW))])
    assert other[0]["track_id"] == 2


def test_two_people_on_screen_never_share_an_id():
    clock = Clock()
    resolver = IdentityResolver("CAM-T", clock=clock)
    frame = blank()
    out = resolver.resolve(frame, [
        detection(1, person(frame, 50, 100, RED, BLUE)),
        detection(2, person(frame, 400, 100, RED, BLUE)),  # dressed identically
    ])
    assert {d["track_id"] for d in out} == {1, 2}
    clock.now = 1
    frame = blank()
    again = resolver.resolve(frame, [
        detection(1, person(frame, 55, 100, RED, BLUE)),
        detection(2, person(frame, 405, 100, RED, BLUE)),
    ])
    assert [d["track_id"] for d in again] == [1, 2], "live tracks keep their identities"


def test_identity_expires_after_the_window():
    clock = Clock()
    resolver = IdentityResolver("CAM-T", window_seconds=60, clock=clock)
    frame = blank()
    resolver.resolve(frame, [detection(1, person(frame, 100, 100, RED, BLUE))])
    clock.now = 120
    frame = blank()
    out = resolver.resolve(frame, [detection(9, person(frame, 100, 100, RED, BLUE))])
    assert out[0]["track_id"] == 2
    assert resolver.identity_count == 1


def test_other_classes_are_not_merged_with_people():
    clock = Clock()
    resolver = IdentityResolver("CAM-T", clock=clock)
    frame = blank()
    resolver.resolve(frame, [detection(1, person(frame, 100, 100, RED, BLUE))])
    clock.now = 5
    frame = blank()
    car = dict(person(frame, 100, 100, RED, BLUE), cls_name="car")
    assert resolver.resolve(frame, [detection(2, car)])[0]["track_id"] == 2
