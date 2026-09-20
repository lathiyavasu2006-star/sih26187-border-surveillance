"""Alert triage endpoints and event/track history."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.core.api_config import api_settings
from backend.core.enums import AlertType, RiskLevel
from backend.models import Alert, AuditLog, Event, TrackedObject
from backend.models.alert import generate_alert_id

from tests.api.conftest import auth_header


async def seed_alert(session, camera_id="CAM-N-001", risk_score=85, alert_type=AlertType.INTRUSION,
                     acknowledged=False, when=None, track_id=7):
    alert = Alert(
        alert_id=generate_alert_id(when),
        camera_id=camera_id,
        track_id=track_id,
        alert_type=alert_type,
        risk_score=risk_score,
        risk_level=RiskLevel(
            "critical" if risk_score > 80 else "high_risk" if risk_score > 60 else "suspicious" if risk_score > 40 else "normal"
        ),
        risk_reasons=["restricted zone"],
        acknowledged=acknowledged,
        acknowledged_at=datetime.now(timezone.utc) if acknowledged else None,
        timestamp=when or datetime.now(timezone.utc),
    )
    session.add(alert)
    await session.commit()
    return alert


async def test_list_alerts_ordering_and_counts(client, camera, admin_headers, session):
    now = datetime.now(timezone.utc)
    old_unack = await seed_alert(session, when=now - timedelta(hours=2), risk_score=50)
    newest_unack = await seed_alert(session, when=now, risk_score=95)
    acked = await seed_alert(session, when=now - timedelta(minutes=1), acknowledged=True)

    response = await client.get("/alerts", headers=admin_headers)
    body = response.json()
    assert body["total"] == 3 and body["unacknowledged_count"] == 2
    # unacknowledged first, newest first within each group
    assert [item["alert_id"] for item in body["items"]] == [newest_unack.alert_id, old_unack.alert_id, acked.alert_id]


async def test_alert_filters(client, camera, admin_headers, session):
    now = datetime.now(timezone.utc)
    await seed_alert(session, risk_score=95, alert_type=AlertType.INTRUSION, when=now)
    await seed_alert(session, risk_score=45, alert_type=AlertType.LOITERING, when=now - timedelta(hours=5))

    critical = await client.get("/alerts", headers=admin_headers, params={"risk_level": "critical"})
    assert critical.json()["total"] == 1

    loitering = await client.get("/alerts", headers=admin_headers, params={"alert_type": "loitering"})
    assert loitering.json()["total"] == 1

    recent = await client.get("/alerts", headers=admin_headers,
                              params={"date_from": (now - timedelta(hours=1)).isoformat()})
    assert recent.json()["total"] == 1

    bad_range = await client.get("/alerts", headers=admin_headers,
                                 params={"date_from": now.isoformat(), "date_to": (now - timedelta(days=1)).isoformat()})
    assert bad_range.status_code == 422

    paged = await client.get("/alerts", headers=admin_headers, params={"skip": 1, "limit": 1})
    assert len(paged.json()["items"]) == 1 and paged.json()["total"] == 2


async def test_alert_detail_and_access_control(client, camera, admin_headers, outsider, session):
    alert = await seed_alert(session)
    detail = await client.get(f"/alerts/{alert.alert_id}", headers=admin_headers)
    assert detail.status_code == 200 and detail.json()["risk_reasons"] == ["restricted zone"]

    assert (await client.get(f"/alerts/{alert.alert_id}", headers=auth_header(outsider))).status_code == 403
    assert (await client.get("/alerts/ALT-NOPE-0000", headers=admin_headers)).status_code == 404


async def test_acknowledge_alert(client, camera, operator, operator_headers, session):
    alert = await seed_alert(session)
    payload = {"acknowledged_by": str(operator.user_id), "false_alarm": True, "notes": "Stray cattle"}

    response = await client.patch(f"/alerts/{alert.alert_id}/acknowledge", headers=operator_headers, json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["acknowledged"] is True and body["false_alarm"] is True
    assert body["acknowledged_by"] == str(operator.user_id)
    assert body["acknowledged_at"] is not None

    # second acknowledgement is a conflict
    again = await client.patch(f"/alerts/{alert.alert_id}/acknowledge", headers=operator_headers, json=payload)
    assert again.status_code == 409

    logs = (await session.execute(select(AuditLog).where(AuditLog.action == "ACKNOWLEDGE_ALERT"))).scalars().all()
    assert any(log.status == "success" and log.old_value and log.new_value for log in logs)


async def test_cannot_acknowledge_as_somebody_else(client, camera, operator_headers, admin, session):
    alert = await seed_alert(session)
    response = await client.patch(
        f"/alerts/{alert.alert_id}/acknowledge",
        headers=operator_headers,
        json={"acknowledged_by": str(admin.user_id), "false_alarm": False},
    )
    assert response.status_code == 403
    assert "your own user_id" in response.json()["detail"]


async def test_false_alarm_requires_notes(client, camera, operator, operator_headers, session):
    alert = await seed_alert(session)
    response = await client.patch(f"/alerts/{alert.alert_id}/acknowledge", headers=operator_headers,
                                  json={"acknowledged_by": str(operator.user_id), "false_alarm": True})
    assert response.status_code == 422


async def test_alert_stats(client, camera, admin_headers, session):
    now = datetime.now(timezone.utc)
    await seed_alert(session, risk_score=95, alert_type=AlertType.INTRUSION, when=now)
    await seed_alert(session, risk_score=95, alert_type=AlertType.WEAPON, when=now)
    await seed_alert(session, risk_score=30, alert_type=AlertType.ANIMAL, when=now, acknowledged=True)

    response = await client.get("/alerts/stats", headers=admin_headers)
    body = response.json()
    assert body["total_today"] == 3
    assert body["critical_today"] == 2
    assert body["unacknowledged"] == 2
    assert body["by_type"]["intrusion"] == 1 and body["by_type"]["weapon"] == 1
    assert body["by_type"]["smoke"] == 0          # every enum value is present
    assert body["by_risk_level"]["critical"] == 2
    assert body["by_camera"] == [{"camera_id": "CAM-N-001", "count": 3}]
    assert len(body["hourly_trend"]) == 24
    ist_hour = now.astimezone(api_settings.reporting_tz).hour
    assert next(h["count"] for h in body["hourly_trend"] if h["hour"] == ist_hour) == 3
    assert body["timezone"] == "UTC+05:30"


async def test_delete_alert_admin_only_and_evidence_guard(client, camera, admin_headers, operator_headers, session):
    from backend.core.enums import EvidenceType
    from backend.models import Evidence

    alert = await seed_alert(session)
    assert (await client.delete(f"/alerts/{alert.alert_id}", headers=operator_headers)).status_code == 403

    session.add(Evidence(alert_id=alert.alert_id, camera_id="CAM-N-001", evidence_type=EvidenceType.SNAPSHOT,
                         file_path="E:/a.jpg", file_name="a.jpg", file_hash="b" * 64))
    await session.commit()
    blocked = await client.delete(f"/alerts/{alert.alert_id}", headers=admin_headers)
    assert blocked.status_code == 409

    await session.execute(Evidence.__table__.delete())
    await session.commit()
    deleted = await client.delete(f"/alerts/{alert.alert_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert (await session.execute(select(Alert))).scalars().all() == []


# --------------------------------------------------------------------------- events

async def seed_track(session, camera_id="CAM-N-001", track_id=7, points=3, active=True):
    start = datetime.now(timezone.utc) - timedelta(minutes=5)
    event = Event(track_id=track_id, camera_id=camera_id, object_class="person",
                  first_seen=start, last_seen=start, is_active=active)
    for index in range(points):
        event.record_position(100 + index * 10, 200, start + timedelta(seconds=index * 5),
                              zone_name="Fence Line", risk_score=40 + index)
        session.add(TrackedObject(track_id=track_id, camera_id=camera_id, object_class="person",
                                  cx=100 + index * 10, cy=200, zone_name="Fence Line", zone_type="restricted",
                                  risk_score=40 + index, risk_level="suspicious",
                                  timestamp=start + timedelta(seconds=index * 5)))
    session.add(event)
    await session.commit()
    return event


async def test_events_list_active_and_history(client, camera, admin_headers, session):
    await seed_track(session, track_id=7, active=True)
    await seed_track(session, track_id=8, active=False)

    listing = await client.get("/events", headers=admin_headers)
    assert listing.json()["total"] == 2

    active = await client.get("/events/active", headers=admin_headers)
    assert active.json()["total"] == 1
    assert active.json()["items"][0]["track_id"] == 7

    filtered = await client.get("/events", headers=admin_headers, params={"object_class": "person", "is_active": False})
    assert filtered.json()["total"] == 1

    history = await client.get("/events/7", headers=admin_headers, params={"camera_id": "CAM-N-001"})
    body = history.json()
    assert body["track_id"] == 7 and body["total_events"] == 1
    assert body["cameras"] == ["CAM-N-001"]
    assert body["max_risk_score"] == 42
    assert len(body["events"][0]["positions"]) == 3

    assert (await client.get("/events/999", headers=admin_headers)).status_code == 404


async def test_event_timeline_prefers_tracked_objects(client, camera, admin_headers, session):
    await seed_track(session, track_id=7)
    response = await client.get("/events/7/timeline", headers=admin_headers)
    body = response.json()
    assert body["source"] == "tracked_objects"
    assert len(body["points"]) == 3
    assert body["points"][0]["zone_type"] == "restricted"
    assert [point["cx"] for point in body["points"]] == [100, 110, 120]
    stamps = [point["timestamp"] for point in body["points"]]
    assert stamps == sorted(stamps)


async def test_event_timeline_falls_back_to_positions(client, camera, admin_headers, session):
    await seed_track(session, track_id=9)
    await session.execute(TrackedObject.__table__.delete())
    await session.commit()

    response = await client.get("/events/9/timeline", headers=admin_headers)
    body = response.json()
    assert body["source"] == "event_positions"
    assert len(body["points"]) == 3


async def test_events_respect_camera_scope(client, camera, outsider, session):
    await seed_track(session)
    response = await client.get("/events", headers=auth_header(outsider))
    assert response.json()["total"] == 0
