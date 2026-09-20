"""GET /cameras/host-location — console host position from the Windows location service."""
import json

import pytest
from sqlalchemy import select

from backend.models import AuditLog
from backend.services import host_location as host_module


def script_output(**overrides) -> str:
    data = {"started": True, "status": "Ready", "permission": "Granted", "known": True,
            "lat": 21.2163412, "lng": 72.8641987, "accuracy": 212.4}
    data.update(overrides)
    return "WARNING: noise before the payload\n" + json.dumps(data)


@pytest.fixture
def fake_location(monkeypatch):
    """Replace the PowerShell call; the platform check is forced to Windows so CI on Linux runs the same path."""
    calls = []

    def configure(output: str):
        def _run() -> str:
            calls.append(output)
            return output

        monkeypatch.setattr(host_module, "_run_powershell", _run)
        monkeypatch.setattr(host_module, "_supported", lambda: True)
        return calls

    host_module.clear_cache()
    yield configure
    host_module.clear_cache()


async def test_host_location_returns_fix_and_audits(client, supervisor_headers, session, fake_location):
    calls = fake_location(script_output())
    response = await client.get("/cameras/host-location", headers=supervisor_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lat"] == pytest.approx(21.216341) and body["lng"] == pytest.approx(72.864199)
    assert body["accuracy_m"] == 212 and body["source"] == "windows-location-service"

    # Cached: a second click inside a minute does not spawn another process; refresh=true does.
    assert (await client.get("/cameras/host-location", headers=supervisor_headers)).status_code == 200
    assert len(calls) == 1
    assert (await client.get("/cameras/host-location?refresh=true", headers=supervisor_headers)).status_code == 200
    assert len(calls) == 2

    audits = (await session.execute(select(AuditLog).where(AuditLog.action == "READ_HOST_LOCATION"))).scalars().all()
    assert len(audits) == 3 and all(entry.status == "success" for entry in audits)
    assert audits[0].new_value == {"source": "windows-location-service", "accuracy_m": 212}


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"permission": "Denied", "known": False, "lat": None, "lng": None}, "Location is turned off"),
        ({"status": "Disabled", "known": False, "lat": None, "lng": None}, "disabled"),
        ({"status": "Initializing", "known": False, "lat": None, "lng": None}, "could not determine"),
        ({"lat": 123.0}, "invalid position"),
    ],
)
async def test_host_location_unavailable_is_503_with_reason(client, supervisor_headers, session, fake_location, overrides, fragment):
    fake_location(script_output(**overrides))
    response = await client.get("/cameras/host-location", headers=supervisor_headers)
    assert response.status_code == 503
    assert fragment in response.json()["detail"]
    audit = (await session.execute(select(AuditLog).where(AuditLog.action == "READ_HOST_LOCATION"))).scalars().one()
    assert audit.status == "failed" and fragment in audit.new_value["reason"]


async def test_host_location_garbage_output(client, supervisor_headers, fake_location):
    fake_location("not json at all")
    response = await client.get("/cameras/host-location", headers=supervisor_headers)
    assert response.status_code == 503 and "no data" in response.json()["detail"]


async def test_host_location_requires_supervisor(client, operator_headers, fake_location):
    calls = fake_location(script_output())
    assert (await client.get("/cameras/host-location", headers=operator_headers)).status_code == 403
    assert (await client.get("/cameras/host-location")).status_code == 401
    assert calls == []


async def test_host_location_non_windows(client, supervisor_headers, monkeypatch):
    host_module.clear_cache()
    monkeypatch.setattr(host_module, "_supported", lambda: False)
    response = await client.get("/cameras/host-location", headers=supervisor_headers)
    assert response.status_code == 503 and "Windows" in response.json()["detail"]


async def test_route_does_not_shadow_camera_detail(client, camera, supervisor_headers):
    response = await client.get(f"/cameras/{camera['camera_id']}", headers=supervisor_headers)
    assert response.status_code == 200 and response.json()["camera_id"] == camera["camera_id"]
