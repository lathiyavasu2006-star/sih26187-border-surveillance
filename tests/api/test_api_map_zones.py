"""Zones drawn on the map: camera ground-plane calibration and the projection into the pixel fence."""
import math

import pytest
from sqlalchemy import select

from backend.models import AuditLog, Camera, Zone
from backend.services import geo_calibration

# A synthetic camera 8 m above flat ground, tilted 65° from vertical, looking north. Landmarks are produced
# from the true projection so the fitted homography can be checked against a known answer.
ORIGIN = (26.98123456, 84.85123456)  # the camera fixture's GPS
FOCAL, CENTRE = 900.0, (960.0, 540.0)
TILT = math.radians(65)
HEIGHT_M = 8.0
FRAME = [1920, 1080]


def pixel_of(east: float, north: float) -> list:
    """True image position of a point on the ground, in pixels."""
    y_cam = north * math.cos(TILT) - HEIGHT_M * math.sin(TILT)
    z_cam = north * math.sin(TILT) + HEIGHT_M * math.cos(TILT)
    x = CENTRE[0] + FOCAL * east / z_cam
    y = CENTRE[1] + FOCAL * y_cam / z_cam
    return [round(x, 2), round(y, 2)]


def geo_of(east: float, north: float) -> list:
    lat = ORIGIN[0] + math.degrees(north / geo_calibration.EARTH_RADIUS_M)
    lng = ORIGIN[1] + math.degrees(east / (geo_calibration.EARTH_RADIUS_M * math.cos(math.radians(ORIGIN[0]))))
    return [round(lat, 7), round(lng, 7)]


LANDMARKS = [(-12, 25), (12, 25), (-20, 60), (20, 60)]


def calibration_payload(points=LANDMARKS) -> dict:
    return {
        "points": [{"image": pixel_of(east, north), "geo": geo_of(east, north)} for east, north in points],
        "image_size": FRAME,
    }


def map_zone(camera_id: str, corners=((-8, 30), (8, 30), (8, 50), (-8, 50)), name: str = "Map fence") -> dict:
    return {
        "camera_id": camera_id,
        "zone_name": name,
        "zone_type": "restricted",
        "geo_polygon": [geo_of(east, north) for east, north in corners],
    }


# --------------------------------------------------------------------------- calibration

async def test_calibrate_camera_fits_the_ground_plane(client, camera, supervisor_headers, session):
    response = await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers,
                                json=calibration_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["camera_id"] == camera["camera_id"]
    assert body["error_px"] < 1.0  # the landmarks come from one flat plane, so the fit is near-exact
    assert body["image_size"] == FRAME and body["zones_projected"] == 0

    listed = await client.get("/cameras", headers=supervisor_headers)
    assert listed.json()["items"][0]["calibrated"] is True

    stored = (await session.execute(select(Camera).where(Camera.camera_id == camera["camera_id"]))).scalar_one()
    assert len(stored.calibration["homography"]) == 3
    assert stored.calibration["calibrated_by"] == "supervisor.north"
    audit = (await session.execute(select(AuditLog).where(AuditLog.action == "CALIBRATE_CAMERA"))).scalars().one()
    assert audit.new_value["points"] == 4 and audit.status == "success"


@pytest.mark.parametrize(
    ("points", "fragment"),
    [
        ([(-20, 30), (-10, 30), (0, 30), (10, 30)], "line"),          # collinear landmarks
        ([(-12, 25), (-12, 25), (20, 60), (-20, 60)], "different"),   # duplicate landmark
    ],
)
async def test_bad_calibration_points_are_refused(client, camera, supervisor_headers, session, points, fragment):
    response = await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers,
                                json=calibration_payload(points))
    assert response.status_code == 422
    assert fragment in response.json()["detail"]
    audit = (await session.execute(select(AuditLog).where(AuditLog.action == "CALIBRATE_CAMERA"))).scalars().one()
    assert audit.status == "failed"


async def test_calibration_needs_four_points_inside_the_frame(client, camera, supervisor_headers):
    too_few = {"points": calibration_payload()["points"][:3], "image_size": FRAME}
    assert (await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=too_few)).status_code == 422

    outside = calibration_payload()
    outside["points"][0]["image"] = [5000.0, 50.0]
    response = await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=outside)
    assert response.status_code == 422 and "outside" in response.json()["detail"]


async def test_calibration_read_and_removal(client, camera, supervisor_headers, operator_headers):
    missing = await client.get(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers)
    assert missing.status_code == 404

    await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=calibration_payload())
    read = await client.get(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers)
    assert read.status_code == 200 and len(read.json()["points"]) == 4

    assert (await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=operator_headers, json=calibration_payload())).status_code == 403
    assert (await client.delete(f"/cameras/{camera['camera_id']}/calibration", headers=operator_headers)).status_code == 403

    removed = await client.delete(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers)
    assert removed.status_code == 200
    assert (await client.get(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers)).status_code == 404
    assert (await client.delete(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers)).status_code == 404


# --------------------------------------------------------------------------- zones drawn on the map

async def test_map_zone_is_projected_into_the_camera_frame(client, camera, supervisor_headers, session):
    await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=calibration_payload())

    response = await client.post("/zones", headers=supervisor_headers, json=map_zone(camera["camera_id"]))
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["geo_polygon"]) == 4
    assert len(body["polygon"]) == 4
    # Every corner lands where the true camera projection puts it (within a pixel of rounding).
    for (east, north), pixel in zip(((-8, 30), (8, 30), (8, 50), (-8, 50)), body["polygon"]):
        assert math.dist(pixel, pixel_of(east, north)) < 1.5
    assert body["risk_bonus"] == 50  # restricted zone policy, unchanged by the map route

    stored = (await session.execute(select(Zone).where(Zone.zone_id == body["zone_id"]))).scalar_one()
    assert stored.geo_polygon is not None and stored.polygon == body["polygon"]


async def test_map_zone_needs_a_calibrated_camera(client, camera, supervisor_headers):
    response = await client.post("/zones", headers=supervisor_headers, json=map_zone(camera["camera_id"]))
    assert response.status_code == 409
    assert "not calibrated" in response.json()["detail"]


async def test_area_behind_the_camera_is_refused(client, camera, supervisor_headers):
    await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=calibration_payload())
    behind = map_zone(camera["camera_id"], corners=((-8, -40), (8, -40), (8, -20), (-8, -20)))
    response = await client.post("/zones", headers=supervisor_headers, json=behind)
    assert response.status_code == 422 and "behind the camera" in response.json()["detail"]


async def test_zone_needs_one_polygon_source(client, camera, supervisor_headers):
    response = await client.post("/zones", headers=supervisor_headers,
                                 json={"camera_id": camera["camera_id"], "zone_name": "Empty", "zone_type": "buffer"})
    assert response.status_code == 422
    assert "geo_polygon" in response.text


async def test_recalibration_reprojects_existing_map_zones(client, camera, supervisor_headers, session):
    await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=calibration_payload())
    created = (await client.post("/zones", headers=supervisor_headers, json=map_zone(camera["camera_id"]))).json()

    # The camera is re-aimed: the same landmarks now appear 100 px lower in the frame.
    shifted = calibration_payload()
    for point in shifted["points"]:
        point["image"] = [point["image"][0], point["image"][1] + 100]
    again = await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=shifted)
    assert again.status_code == 200 and again.json()["zones_projected"] == 1

    zone = (await session.execute(select(Zone).where(Zone.zone_id == created["zone_id"]))).scalar_one()
    await session.refresh(zone)
    assert zone.geo_polygon == created["geo_polygon"]  # what the operator drew never changes
    assert [point[1] for point in zone.polygon] == [point[1] + 100 for point in created["polygon"]]


async def test_editing_the_pixel_polygon_detaches_the_zone_from_the_map(client, camera, supervisor_headers, session):
    await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=calibration_payload())
    created = (await client.post("/zones", headers=supervisor_headers, json=map_zone(camera["camera_id"]))).json()

    updated = await client.put(f"/zones/{created['zone_id']}", headers=supervisor_headers,
                               json={"polygon": [[10, 10], [200, 10], [200, 200]]})
    assert updated.status_code == 200
    assert updated.json()["geo_polygon"] is None
    zone = (await session.execute(select(Zone).where(Zone.zone_id == created["zone_id"]))).scalar_one()
    await session.refresh(zone)
    assert zone.geo_polygon is None


async def test_map_zone_can_be_redrawn_on_the_map(client, camera, supervisor_headers):
    await client.put(f"/cameras/{camera['camera_id']}/calibration", headers=supervisor_headers, json=calibration_payload())
    created = (await client.post("/zones", headers=supervisor_headers, json=map_zone(camera["camera_id"]))).json()

    moved = map_zone(camera["camera_id"], corners=((-6, 32), (6, 32), (6, 46), (-6, 46)))
    updated = await client.put(f"/zones/{created['zone_id']}", headers=supervisor_headers,
                               json={"geo_polygon": moved["geo_polygon"]})
    assert updated.status_code == 200
    assert updated.json()["geo_polygon"] == moved["geo_polygon"]
    assert updated.json()["polygon"] != created["polygon"]


# --------------------------------------------------------------------------- the maths itself

def test_projection_matches_the_true_camera():
    calibration = geo_calibration.compute(calibration_payload()["points"], FRAME)
    assert calibration["error_px"] < 1.0
    pixels = geo_calibration.project_polygon(calibration, [geo_of(east, north) for east, north in ((-8, 30), (8, 30), (8, 50))])
    for (east, north), pixel in zip(((-8, 30), (8, 30), (8, 50)), pixels):
        assert math.dist(pixel, pixel_of(east, north)) < 1.5
    assert geo_calibration.covers(calibration, [geo_of(e, n) for e, n in ((-8, 30), (8, 30), (8, 50))])


def test_points_outside_the_field_of_view_are_clamped_not_wrapped():
    calibration = geo_calibration.compute(calibration_payload()["points"], FRAME)
    pixels = geo_calibration.project_polygon(calibration, [geo_of(-400, 30), geo_of(400, 30), geo_of(0, 80)])
    for x, y in pixels:
        assert 0 <= x <= FRAME[0] and 0 <= y <= FRAME[1]
