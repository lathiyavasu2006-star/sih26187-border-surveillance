"""Event memory, cooldowns, re-appearance, direction and loitering."""
from datetime import timedelta

import pytest

from ml.config import ml_config
from ml.direction import compass_heading, direction_detector
from ml.event_memory import EventMemory
from ml.loitering import loitering_detector
from ml.tests.conftest import DAY, NIGHT

RESTRICTED = {"zone_id": 1, "zone_name": "Fence", "zone_type": "restricted", "in_any_fence": True}
PUBLIC = {"zone_id": None, "zone_name": None, "zone_type": "public", "in_any_fence": False}
ZONE = {"zone_id": 1, "loiter_threshold_seconds": 30, "night_rules": {"multiplier": 1.5, "start": 22, "end": 5}}
FENCE = [[400, 0], [600, 0], [600, 400], [400, 400]]


def test_dwell_time_and_zone_history():
    memory = EventMemory()
    for second in range(0, 11):
        memory.update(1, "CAM", 100, 100, PUBLIC, 10, now=DAY + timedelta(seconds=second))
    for second in range(11, 51):
        memory.update(1, "CAM", 450, 100, RESTRICTED, 60, now=DAY + timedelta(seconds=second))

    track = memory.get(1)
    assert memory.get_time_in_zone(1) == 39
    assert memory.get_time_in_zone(1, now=DAY + timedelta(seconds=60)) == 49
    assert track.zone_history == [] or track.zone_history[0]["zone_type"] == "public"
    assert track.total_time_seconds == 50
    assert track.max_risk_score == 60
    assert len(track.trail) <= ml_config.trail_length


def test_zone_exit_is_recorded():
    memory = EventMemory()
    memory.update(1, "CAM", 450, 100, RESTRICTED, 60, now=DAY)
    memory.update(1, "CAM", 450, 100, RESTRICTED, 60, now=DAY + timedelta(seconds=20))
    memory.update(1, "CAM", 100, 100, PUBLIC, 10, now=DAY + timedelta(seconds=21))
    history = memory.get(1).zone_history
    assert history[-1]["zone_name"] == "Fence" and history[-1]["zone_type"] == "restricted"
    assert history[-1]["duration_seconds"] == 21


def test_alert_cooldown_is_per_track_and_type():
    memory = EventMemory()
    memory.update(7, "CAM", 10, 10, PUBLIC, 50, now=DAY)
    assert memory.can_alert(7, "intrusion", 15, now=DAY)
    memory.record_alert(7, "intrusion", now=DAY)

    assert not memory.can_alert(7, "intrusion", 15, now=DAY + timedelta(seconds=14))
    assert memory.can_alert(7, "loitering", 15, now=DAY + timedelta(seconds=1)), "different type is independent"
    assert memory.can_alert(8, "intrusion", 15, now=DAY + timedelta(seconds=1)), "different track is independent"
    assert memory.can_alert(7, "intrusion", 15, now=DAY + timedelta(seconds=15))
    assert memory.get(7).alert_count == 1


def test_speed_measurement():
    memory = EventMemory()
    for i in range(11):
        memory.update(3, "CAM", 100 + i * 30, 200, PUBLIC, 10, now=DAY + timedelta(seconds=i * 0.1))
    assert memory.get_speed(3, window_seconds=1.0) == pytest.approx(300, rel=0.01)


def test_reappearance_counts():
    memory = EventMemory()
    memory.update(1, "CAM", 200, 200, PUBLIC, 10, now=DAY)
    memory.mark_missing_as_lost(set(), now=DAY + timedelta(seconds=5))
    assert memory.get(1).is_active is False

    # ByteTrack gives the same individual a new id near where it vanished.
    memory.update(2, "CAM", 220, 210, PUBLIC, 10, now=DAY + timedelta(seconds=30))
    assert memory.get_appear_count(2) == 2
    memory.mark_missing_as_lost(set(), now=DAY + timedelta(seconds=40))
    memory.update(3, "CAM", 205, 190, PUBLIC, 10, now=DAY + timedelta(seconds=60))
    assert memory.get_appear_count(3) == 3

    # Far away or too long ago: a different individual.
    memory.mark_missing_as_lost(set(), now=DAY + timedelta(seconds=70))
    memory.update(4, "CAM", 900, 900, PUBLIC, 10, now=DAY + timedelta(seconds=75))
    assert memory.get_appear_count(4) == 1


def test_reactivated_track_id_counts_as_reappearance():
    memory = EventMemory()
    memory.update(5, "CAM", 10, 10, PUBLIC, 10, now=DAY)
    memory.mark_lost(5)
    memory.update(5, "CAM", 12, 10, PUBLIC, 10, now=DAY + timedelta(seconds=2))
    assert memory.get(5).is_active and memory.get_appear_count(5) == 2


def test_cleanup_removes_only_old_lost_tracks():
    memory = EventMemory()
    memory.update(1, "CAM", 0, 0, PUBLIC, 0, now=DAY)
    memory.update(2, "CAM", 0, 0, PUBLIC, 0, now=DAY)
    memory.mark_lost(1)
    assert memory.cleanup_old_tracks(max_age_seconds=300, now=DAY + timedelta(seconds=301)) == 1
    assert 1 not in memory and 2 in memory


def trail_memory(points):
    memory = EventMemory()
    for i, (x, y) in enumerate(points):
        memory.update(1, "CAM", x, y, PUBLIC, 0, now=DAY + timedelta(seconds=i * 0.2))
    return memory


def test_direction_relative_to_fence():
    toward = trail_memory([(100 + 30 * i, 200) for i in range(8)])
    assert direction_detector.compute(1, toward, FENCE) == "toward_fence"

    away = trail_memory([(380 - 30 * i, 200) for i in range(8)])
    assert direction_detector.compute(1, away, FENCE) == "away_from_fence"

    parallel = trail_memory([(300, 20 + 40 * i) for i in range(8)])
    assert direction_detector.compute(1, parallel, FENCE) == "parallel"

    still = trail_memory([(100, 100)] * 8)
    assert direction_detector.compute(1, still, FENCE) == "stationary"
    assert direction_detector.compute(1, trail_memory([(1, 1), (2, 2)]), FENCE) == "stationary"


def test_direction_without_fence_and_compass():
    assert direction_detector.compute(1, trail_memory([(100 + 20 * i, 100) for i in range(5)])) == "moving"
    assert compass_heading((0, 100), (0, 0)) == "north"
    assert compass_heading((0, 0), (0, 100)) == "south"
    assert compass_heading((0, 0), (100, 0)) == "east"
    assert compass_heading((100, 100), (0, 0)) == "northwest"
    assert compass_heading((0, 0), (1, 1)) == "stationary"


def test_compass_values_match_backend_enum():
    from backend.core.enums import Direction

    valid = {d.value for d in Direction}
    for start, end in [((0, 0), (100, 0)), ((0, 0), (-70, -70)), ((0, 0), (0, 0)), ((5, 5), (5, 105))]:
        assert compass_heading(start, end) in valid


def test_loitering_thresholds_day_and_night():
    day = loitering_detector.check(1, 29, ZONE, now=DAY)
    assert day == {"loitering": False, "time_in_zone": 29, "threshold_used": 30, "is_night_rules": False}
    assert loitering_detector.check(1, 30, ZONE, now=DAY)["loitering"] is True

    night = loitering_detector.check(1, 21, ZONE, now=NIGHT)
    assert night["threshold_used"] == 20 and night["is_night_rules"] is True and night["loitering"] is True


def test_no_loitering_outside_a_zone():
    result = loitering_detector.check(1, 3600, {}, now=DAY)
    assert result["loitering"] is False and result["threshold_used"] is None
