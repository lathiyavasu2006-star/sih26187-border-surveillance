"""Risk engine: every scoring rule, night multiplier, 100 cap, levels and alert typing."""
import itertools

import pytest

from ml.config import ml_config
from ml.risk_engine import risk_engine
from ml.tests.conftest import DAY, NIGHT

PUBLIC = {"zone_type": "public", "in_any_fence": False}
NO_LOITER = {"loitering": False}


def score(detection=None, zone=None, loiter=None, direction="stationary", appear=1, dwell=0, now=DAY):
    return risk_engine.calculate(detection or {}, zone or PUBLIC, loiter or NO_LOITER, direction, appear, dwell, now=now)


def test_base_score_only():
    result = score()
    assert result == {"risk_score": 10, "risk_level": "normal", "risk_reasons": []}


@pytest.mark.parametrize("zone_type,expected", [
    ("public", 10), ("buffer", 20), ("sensitive", 40), ("restricted", 60), ("no_mans_land", 100),
])
def test_zone_bonus(zone_type, expected):
    result = score(zone={"zone_type": zone_type, "in_any_fence": zone_type != "public"})
    assert result["risk_score"] == expected
    bonus = ml_config.zone_risk_bonus[zone_type]
    if bonus:
        assert f"zone_{zone_type}+{bonus}" in result["risk_reasons"]  # 10 + 100 is capped, the reason is not


def test_zone_bonus_uses_backend_value_when_present():
    assert score(zone={"zone_type": "restricted", "risk_bonus": 50})["risk_score"] == 60


def test_loitering_adds_20():
    result = score(loiter={"loitering": True, "threshold_used": 30}, dwell=45)
    assert result["risk_score"] == 30
    assert result["risk_reasons"][0].startswith("loitering+20(threshold:30s")


def test_toward_fence_adds_20_but_other_directions_do_not():
    assert score(direction="toward_fence")["risk_score"] == 30
    for direction in ("away_from_fence", "parallel", "moving", "stationary"):
        assert score(direction=direction)["risk_score"] == 10


def test_repeat_appearance_threshold():
    assert score(appear=2)["risk_score"] == 10
    assert score(appear=3)["risk_score"] == 25
    assert "repeat_appearance+15(count:4)" in score(appear=4)["risk_reasons"]


def test_running_and_weapon():
    assert score(detection={"is_running": True})["risk_score"] == 35
    weapon = score(detection={"weapon_detected": True, "weapon_class": "knife"})
    assert weapon["risk_score"] == 60
    assert "weapon_detected+50(knife)" in weapon["risk_reasons"]


def test_night_multiplier():
    day = score(zone={"zone_type": "sensitive"}, now=DAY)
    night = score(zone={"zone_type": "sensitive"}, now=NIGHT)
    assert day["risk_score"] == 40 and day["risk_level"] == "low"
    assert night["risk_score"] == 60 and night["risk_level"] == "suspicious"
    assert f"night_multiplier_x{ml_config.night_risk_multiplier}" in night["risk_reasons"]
    assert not any("night" in r for r in day["risk_reasons"])


def test_specification_scenario_caps_at_100():
    result = risk_engine.calculate(
        {"is_running": False, "weapon_detected": False},
        {"zone_type": "restricted", "risk_bonus": 50},
        {"loitering": True, "threshold_used": 30},
        "toward_fence", 4, 60, now=DAY,
    )
    # 10 + 50 + 20 + 20 + 15 = 115 -> capped
    assert result["risk_score"] == 100
    assert result["risk_level"] == "critical"
    assert len(result["risk_reasons"]) == 4


def test_score_never_exceeds_100_or_drops_below_0():
    zones = ["public", "buffer", "sensitive", "restricted", "no_mans_land"]
    for zone_type, loiter, direction, appear, running, weapon, now in itertools.product(
        zones, (False, True), ("toward_fence", "stationary"), (1, 5), (False, True), (False, True), (DAY, NIGHT)
    ):
        result = score({"is_running": running, "weapon_detected": weapon}, {"zone_type": zone_type},
                       {"loitering": loiter, "threshold_used": 30}, direction, appear, 60, now)
        assert 0 <= result["risk_score"] <= 100
        assert result["risk_level"] == ml_config.risk_level_for(result["risk_score"])


@pytest.mark.parametrize("value,level", [
    (0, "normal"), (20, "normal"), (21, "low"), (40, "low"), (41, "suspicious"), (60, "suspicious"),
    (61, "high_risk"), (80, "high_risk"), (81, "critical"), (100, "critical"),
])
def test_level_boundaries_match_backend(value, level):
    from backend.core.config import settings

    assert risk_engine._get_level(value) == level
    assert settings.get_risk_level(value) == level, "ML and backend must agree or the backend rejects alerts"


def test_should_create_alert():
    assert [lvl for lvl in ml_config.risk_levels if risk_engine.should_create_alert(lvl)] == \
        ["suspicious", "high_risk", "critical"]


def test_alert_types_are_valid_backend_enum_values():
    from backend.core.enums import AlertType

    valid = {a.value for a in AlertType}
    cases = [
        ({"cls_name": "person", "weapon_detected": True}, {"in_any_fence": True, "zone_type": "restricted"}, NO_LOITER, "weapon"),
        ({"cls_name": "cow"}, {"in_any_fence": True, "zone_type": "no_mans_land"}, NO_LOITER, "animal"),
        ({"cls_name": "person"}, {"in_any_fence": True, "zone_type": "restricted"}, {"loitering": True}, "loitering"),
        ({"cls_name": "person"}, {"in_any_fence": True, "zone_type": "restricted"}, NO_LOITER, "intrusion"),
        ({"cls_name": "truck"}, {"in_any_fence": True, "zone_type": "no_mans_land"}, NO_LOITER, "vehicle"),
        ({"cls_name": "person"}, {"in_any_fence": True, "zone_type": "buffer"}, NO_LOITER, "zone_breach"),
        ({"cls_name": "person"}, PUBLIC, NO_LOITER, "behavior"),
    ]
    for detection, zone_info, loiter, expected in cases:
        alert_type = risk_engine.alert_type_for(detection, zone_info, loiter)
        assert alert_type == expected
        assert alert_type in valid
