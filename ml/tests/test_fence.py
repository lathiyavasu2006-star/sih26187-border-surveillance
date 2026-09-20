"""Virtual fence: point-in-polygon, overlapping zones, backend cache behaviour."""
import pytest

from ml.fence import FenceChecker

SQUARE = [[100, 100], [300, 100], [300, 300], [100, 300]]


def zone(zone_id, polygon, zone_type="restricted", risk_bonus=None, active=True, name=None):
    return {"zone_id": zone_id, "zone_name": name or f"Zone {zone_id}", "zone_type": zone_type,
            "polygon": polygon, "risk_bonus": risk_bonus, "is_active": active,
            "loiter_threshold_seconds": 30, "night_rules": {"multiplier": 1.5, "start": 22, "end": 5}}


@pytest.fixture
def checker(backend_client):
    return FenceChecker(client=backend_client)


def test_inside_outside_and_edge(checker):
    zones = [zone(1, SQUARE, "restricted", 50)]
    inside = checker.check_point(200, 200, zones)
    assert inside == {"in_any_fence": True, "zone_id": 1, "zone_name": "Zone 1", "zone_type": "restricted",
                      "risk_bonus": 50}

    outside = checker.check_point(50, 50, zones)
    assert outside["in_any_fence"] is False and outside["zone_type"] == "public" and outside["risk_bonus"] == 0

    assert checker.check_point(100, 200, zones)["in_any_fence"] is True   # on the boundary counts as inside
    assert checker.check_point(301, 200, zones)["in_any_fence"] is False


def test_concave_polygon(checker):
    l_shape = [[0, 0], [200, 0], [200, 100], [100, 100], [100, 200], [0, 200]]
    zones = [zone(1, l_shape, "sensitive", 30)]
    assert checker.check_point(50, 150, zones)["in_any_fence"] is True
    assert checker.check_point(150, 150, zones)["in_any_fence"] is False   # the notch


def test_highest_risk_zone_wins_when_overlapping(checker):
    zones = [
        zone(1, [[0, 0], [400, 0], [400, 400], [0, 400]], "buffer", 10),
        zone(2, SQUARE, "no_mans_land", 100),
        zone(3, [[150, 150], [250, 150], [250, 250]], "sensitive", 30),
    ]
    assert checker.check_point(200, 180, zones)["zone_id"] == 2
    assert checker.check_point(20, 20, zones)["zone_type"] == "buffer"


def test_risk_bonus_defaults_from_zone_type(checker):
    result = checker.check_point(200, 200, [zone(1, SQUARE, "sensitive", None)])
    assert result["risk_bonus"] == 30


def test_inactive_and_degenerate_zones_ignored(checker):
    zones = [zone(1, SQUARE, active=False), zone(2, [[0, 0], [10, 10]])]
    assert checker.check_point(200, 200, zones)["in_any_fence"] is False
    assert checker.check_point(5, 5, zones)["in_any_fence"] is False


def test_most_sensitive_polygon(checker):
    zones = [zone(1, [[0, 0], [9, 0], [9, 9]], "buffer", 10), zone(2, SQUARE, "restricted", 50)]
    assert checker.most_sensitive_polygon(zones) == SQUARE
    assert checker.most_sensitive_polygon([]) is None


async def test_zones_are_cached_for_30_seconds(checker, fake_backend, monkeypatch):
    fake_backend.zones = [zone(1, SQUARE)]
    clock = {"now": 1000.0}
    monkeypatch.setattr("ml.fence.time.monotonic", lambda: clock["now"])

    first = await checker.load_zones("CAM-N-001")
    assert first[0]["zone_id"] == 1 and fake_backend.zone_requests == 1

    clock["now"] += 29
    await checker.load_zones("CAM-N-001")
    assert fake_backend.zone_requests == 1, "must not hit the backend within the cache window"

    fake_backend.zones = [zone(1, SQUARE), zone(2, SQUARE, "buffer")]
    clock["now"] += 2  # 31 s after the first load
    refreshed = await checker.load_zones("CAM-N-001")
    assert fake_backend.zone_requests == 2 and len(refreshed) == 2
    assert fake_backend.logins == 1, "one login shared across requests"


async def test_backend_failure_keeps_last_known_zones(checker, fake_backend, monkeypatch):
    fake_backend.zones = [zone(1, SQUARE)]
    clock = {"now": 0.0}
    monkeypatch.setattr("ml.fence.time.monotonic", lambda: clock["now"])
    await checker.load_zones("CAM-N-001")

    fake_backend.fail_zone_requests = True
    clock["now"] += 31
    zones = await checker.load_zones("CAM-N-001")
    assert [z["zone_id"] for z in zones] == [1], "fence must stay enforced when the backend is unreachable"


async def test_inactive_zones_filtered_on_load(checker, fake_backend):
    fake_backend.zones = [zone(1, SQUARE), zone(2, SQUARE, active=False)]
    zones = await checker.load_zones("CAM-N-002", force=True)
    assert [z["zone_id"] for z in zones] == [1]
