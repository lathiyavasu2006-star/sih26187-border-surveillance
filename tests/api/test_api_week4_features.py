"""Week 4 follow-up endpoints: camera metadata edit, test-data clearing, evidence playback, video analysis."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from backend.core.enums import AlertType
from backend.models import Alert, AuditLog, Camera, Evidence
from backend.services import analysis_jobs as analysis_module
from backend.services.analysis_jobs import analysis_jobs, high_risk_moments

from tests.api.conftest import auth_header, jpeg_bytes
from tests.api.test_api_alerts_events import seed_alert
from tests.api.test_api_evidence import upload


def mpeg4_part2_video(path: Path, frames: int = 20, fps: float = 10.0) -> bytes:
    """A real MP4 in the codec OpenCV's "mp4v" writes (what the ML pipeline produced before Week 4)."""
    writer = cv2.VideoWriter(str(path), cv2.CAP_FFMPEG, cv2.VideoWriter_fourcc(*"mp4v"), fps, (160, 120))
    assert writer.isOpened()
    for index in range(frames):
        frame = np.zeros((120, 160, 3), np.uint8)
        cv2.putText(frame, str(index), (50, 70), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 2)
        writer.write(frame)
    writer.release()
    return path.read_bytes()


# --------------------------------------------------------------------------- PATCH /cameras/{id}

async def test_edit_camera_gps_and_location(client, camera, supervisor_headers, session):
    response = await client.patch(
        f"/cameras/{camera['camera_id']}",
        headers=supervisor_headers,
        json={"gps_lat": 32.7266, "gps_lng": 74.857, "location_name": "J&K Border - Sector 7", "camera_type": "ptz"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["gps_lat"], body["gps_lng"], body["location_name"], body["camera_type"]) == (32.7266, 74.857, "J&K Border - Sector 7", "ptz")
    assert body["name"] == camera["name"] and body["camera_id"] == camera["camera_id"], "unsent fields are unchanged"
    assert "password" not in json.dumps(body)

    stored = await session.get(Camera, camera["camera_id"])
    await session.refresh(stored)
    assert float(stored.gps_lat) == pytest.approx(32.7266)
    audit = (await session.execute(select(AuditLog).where(AuditLog.action == "EDIT_CAMERA"))).scalars().one()
    assert audit.new_value["location_name"] == "J&K Border - Sector 7"
    assert audit.old_value["gps_lat"] == pytest.approx(26.98123456)


@pytest.mark.parametrize("payload, fragment", [
    ({}, "No fields"),
    ({"gps_lat": 32.7}, "together"),
    ({"gps_lat": 95, "gps_lng": 74}, "less than or equal"),
    ({"name": None}, "cannot be null"),
    ({"status": "offline"}, "Extra inputs"),
    ({"rtsp_url": "rtsp://x"}, "Extra inputs"),
])
async def test_edit_camera_validation(client, camera, supervisor_headers, payload, fragment):
    response = await client.patch(f"/cameras/{camera['camera_id']}", headers=supervisor_headers, json=payload)
    assert response.status_code == 422
    assert fragment in response.text


async def test_edit_camera_permissions(client, camera, operator_headers, outsider):
    denied = await client.patch(f"/cameras/{camera['camera_id']}", headers=operator_headers, json={"name": "x"})
    assert denied.status_code == 403
    scoped = await client.patch(f"/cameras/{camera['camera_id']}", headers=auth_header(outsider), json={"name": "x"})
    assert scoped.status_code in (403, 404)
    missing = await client.patch("/cameras/CAM-N-999", headers=auth_header(outsider), json={"name": "x"})
    assert missing.status_code in (403, 404)


async def test_gps_can_be_cleared_together(client, camera, supervisor_headers):
    response = await client.patch(f"/cameras/{camera['camera_id']}", headers=supervisor_headers,
                                  json={"gps_lat": None, "gps_lng": None, "sector_name": ""})
    assert response.status_code == 200
    assert response.json()["gps_lat"] is None and response.json()["sector_name"] is None


# --------------------------------------------------------------------------- POST /alerts/clear-test-data

async def test_clear_test_data_closes_only_old_open_alerts(client, camera, admin_headers, operator_headers, session):
    old_open = await seed_alert(session, when=datetime.now(timezone.utc) - timedelta(days=2))
    old_closed = await seed_alert(session, when=datetime.now(timezone.utc) - timedelta(days=2, seconds=1), acknowledged=True)
    today_open = await seed_alert(session, alert_type=AlertType.LOITERING)

    assert (await client.post("/alerts/clear-test-data", headers=operator_headers)).status_code == 403
    response = await client.post("/alerts/clear-test-data", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["cleared"] == 1

    session.expire_all()
    rows = {alert.alert_id: alert for alert in (await session.execute(select(Alert))).scalars()}
    assert len(rows) == 3, "nothing is deleted (evidence references alerts)"
    cleared = rows[old_open.alert_id]
    assert cleared.acknowledged and cleared.false_alarm and "test data" in cleared.notes
    assert rows[today_open.alert_id].acknowledged is False
    assert rows[old_closed.alert_id].false_alarm is False
    audit = (await session.execute(select(AuditLog).where(AuditLog.action == "CLEAR_TEST_ALERTS"))).scalars().one()
    assert audit.new_value["cleared"] == 1 and old_open.alert_id in audit.new_value["alert_ids"]


# --------------------------------------------------------------------------- GET /evidence/{id}/playback

async def test_playback_serves_h264_preview_without_touching_evidence(client, camera, supervisor_headers, session, tmp_path):
    content = mpeg4_part2_video(tmp_path / "legacy.mp4")
    uploaded = await upload(client, supervisor_headers, content, filename="legacy.mp4")
    assert uploaded.status_code == 201, uploaded.text
    evidence_id = uploaded.json()["evidence_id"]

    response = await client.get(f"/evidence/{evidence_id}/playback", headers=supervisor_headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["x-evidence-playback"] == "h264-preview"
    preview = tmp_path / "preview.mp4"
    preview.write_bytes(response.content)
    capture = cv2.VideoCapture(str(preview))
    code = int(capture.get(cv2.CAP_PROP_FOURCC))
    frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    capture.release()
    assert "".join(chr((code >> 8 * i) & 0xFF) for i in range(4)).lower() in ("avc1", "h264")
    assert frames == 20

    record = await session.get(Evidence, evidence_id)
    assert hashlib.sha256(Path(record.file_path).read_bytes()).hexdigest() == record.file_hash, "evidence unchanged"
    verify = await client.get(f"/evidence/{evidence_id}/verify", headers=supervisor_headers)
    assert verify.json()["integrity"] == "valid"

    again = await client.get(f"/evidence/{evidence_id}/playback", headers=supervisor_headers)
    assert again.status_code == 200 and again.content == response.content, "preview is cached"


async def test_playback_rejects_images_and_anonymous(client, camera, supervisor_headers):
    image = await upload(client, supervisor_headers, jpeg_bytes(), filename="still.jpg")
    evidence_id = image.json()["evidence_id"]
    assert (await client.get(f"/evidence/{evidence_id}/playback", headers=supervisor_headers)).status_code == 400
    assert (await client.get(f"/evidence/{evidence_id}/playback")).status_code == 401


# --------------------------------------------------------------------------- video analysis jobs

class FakeWorker:
    """Stands in for `python -m ml.analysis_job`: writes the job file the way the worker does."""

    result: dict = {}
    hold = False

    def __init__(self, command, **_kwargs):
        self.command = command
        self.job_file = Path(command[command.index("--job-file") + 1])
        self.pid = 4242
        self.done = False

    def poll(self):
        if FakeWorker.hold:
            self.job_file.write_text(json.dumps({"status": "running", "progress": {"percent": 40.0, "frames_read": 8,
                                                 "frames_total": 20, "persons": 1}}), encoding="utf-8")
            return None
        if not self.done:
            self.job_file.write_text(json.dumps({"status": "complete", "progress": {"percent": 100.0, "persons": 2},
                                                 "result": FakeWorker.result, "error": None}), encoding="utf-8")
            self.done = True
        return 0

    def kill(self):
        self.done = True


@pytest.fixture
def fake_worker(monkeypatch):
    monkeypatch.setattr(analysis_module.subprocess, "Popen", FakeWorker)
    monkeypatch.setattr(analysis_module, "POLL_SECONDS", 0.01)
    FakeWorker.hold = False
    analysis_jobs.jobs.clear()
    yield FakeWorker
    FakeWorker.hold = False
    analysis_jobs.jobs.clear()


async def wait_for(client, headers, job_id, status="complete"):
    for _ in range(300):
        body = (await client.get(f"/evidence/analysis-jobs/{job_id}", headers=headers)).json()
        if body["status"] in (status, "failed"):
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("analysis job did not finish")


async def test_analysis_creates_high_risk_alerts_with_verified_snapshots(client, camera, supervisor_headers, session,
                                                                        fake_worker, api_environment, tmp_path):
    video = await upload(client, supervisor_headers, mpeg4_part2_video(tmp_path / "patrol.mp4"), filename="patrol.mp4")
    evidence_id = video.json()["evidence_id"]
    snapshot = api_environment / "snapshots" / "ALT-20260918101010-aaaaaaaaaaaaaaaa.jpg"
    snapshot.write_bytes(jpeg_bytes())
    fake_worker.result = {
        "persons_found": [1, 2], "vehicles_found": [5], "animals_found": [],
        "alerts_fired": [
            {"alert_id": "ALT-20260918101010-aaaaaaaaaaaaaaaa", "alert_type": "intrusion", "track_id": 1,
             "video_seconds": 3.5, "risk_score": 95, "risk_level": "critical", "risk_reasons": ["zone_no_mans_land+100"],
             "snapshot_path": str(snapshot)},
            {"alert_id": "ALT-20260918101011-bbbbbbbbbbbbbbbb", "alert_type": "behavior", "track_id": 2,
             "video_seconds": 4.0, "risk_score": 45, "risk_level": "suspicious", "risk_reasons": []},
        ],
        "evidence_saved": [{"alert_id": "ALT-20260918101010-aaaaaaaaaaaaaaaa",
                            "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}],
        "timeline": [{"video_seconds": 3.0, "max_risk_score": 90}, {"video_seconds": 3.5, "max_risk_score": 95},
                     {"video_seconds": 9.0, "max_risk_score": 30}],
        "duration_seconds": 10.0, "frames_read": 20, "frames_processed": 20, "fps": 10.0,
        "resolution": [160, 120], "zones_used": 1,
    }

    started = await client.post(f"/evidence/{evidence_id}/analyze", headers=supervisor_headers)
    assert started.status_code == 202, started.text
    job = await wait_for(client, supervisor_headers, started.json()["job_id"])
    assert job["status"] == "complete", job
    assert job["summary"]["persons_found"] == 2 and job["summary"]["vehicles_found"] == 1
    assert job["summary"]["alerts_detected"] == 2 and job["summary"]["alerts_created"] == 1
    assert job["summary"]["high_risk_moments"] == [{"start_seconds": 3.0, "end_seconds": 3.5, "peak_risk": 95}]
    assert job["alerts_created"] == ["ALT-20260918101010-aaaaaaaaaaaaaaaa"]

    session.expire_all()
    alert = await session.get(Alert, "ALT-20260918101010-aaaaaaaaaaaaaaaa")
    assert alert is not None and alert.camera_id == camera["camera_id"] and alert.snapshot_path
    assert await session.get(Alert, "ALT-20260918101011-bbbbbbbbbbbbbbbb") is None, "suspicious findings are not alerted"
    snapshot_record = (await session.execute(select(Evidence).where(Evidence.alert_id == alert.alert_id))).scalars().one()
    assert snapshot_record.file_hash == hashlib.sha256(snapshot.read_bytes()).hexdigest()

    status = await client.get(f"/evidence/{evidence_id}/analysis-status", headers=supervisor_headers)
    assert status.status_code == 200 and status.json()["job_id"] == job["job_id"]
    actions = {row.action for row in (await session.execute(select(AuditLog))).scalars()}
    assert {"VIDEO_ANALYSIS_STARTED", "VIDEO_ANALYSIS_COMPLETE", "ALERT_CREATED"} <= actions


async def test_analysis_rejects_tampered_snapshot(client, camera, supervisor_headers, session, fake_worker, api_environment, tmp_path):
    video = await upload(client, supervisor_headers, mpeg4_part2_video(tmp_path / "v.mp4"), filename="v.mp4")
    snapshot = api_environment / "snapshots" / "ALT-20260918101012-cccccccccccccccc.jpg"
    snapshot.write_bytes(jpeg_bytes())
    fake_worker.result = {
        "persons_found": [1], "vehicles_found": [], "animals_found": [], "timeline": [],
        "alerts_fired": [{"alert_id": "ALT-20260918101012-cccccccccccccccc", "alert_type": "intrusion", "track_id": 1,
                          "video_seconds": 1.0, "risk_score": 90, "risk_level": "critical", "snapshot_path": str(snapshot)}],
        "evidence_saved": [{"alert_id": "ALT-20260918101012-cccccccccccccccc", "sha256": "0" * 64}],
    }
    started = await client.post(f"/evidence/{video.json()['evidence_id']}/analyze", headers=supervisor_headers)
    job = await wait_for(client, supervisor_headers, started.json()["job_id"])
    assert job["status"] == "complete" and job["evidence_created"] == []
    session.expire_all()
    records = (await session.execute(select(Evidence).where(Evidence.alert_id == "ALT-20260918101012-cccccccccccccccc"))).scalars().all()
    assert records == [], "a snapshot whose hash changed after the worker wrote it is never recorded as evidence"


async def test_only_one_analysis_at_a_time_and_permissions(client, camera, supervisor_headers, operator_headers, fake_worker, tmp_path):
    first = await upload(client, supervisor_headers, mpeg4_part2_video(tmp_path / "a.mp4"), filename="a.mp4")
    evidence_id = first.json()["evidence_id"]
    assert (await client.post(f"/evidence/{evidence_id}/analyze", headers=operator_headers)).status_code == 403

    fake_worker.hold = True
    running = await client.post(f"/evidence/{evidence_id}/analyze", headers=supervisor_headers)
    assert running.status_code == 202
    await asyncio.sleep(0.05)
    progress = (await client.get(f"/evidence/analysis-jobs/{running.json()['job_id']}", headers=supervisor_headers)).json()
    assert progress["status"] == "running" and progress["percent"] == 40.0 and progress["persons"] == 1
    busy = await client.post(f"/evidence/{evidence_id}/analyze", headers=supervisor_headers)
    assert busy.status_code == 409
    fake_worker.result = {"persons_found": [], "vehicles_found": [], "animals_found": [], "alerts_fired": [], "timeline": []}
    fake_worker.hold = False
    assert (await wait_for(client, supervisor_headers, running.json()["job_id"]))["status"] == "complete"

    image = await upload(client, supervisor_headers, jpeg_bytes(), filename="still.jpg")
    assert (await client.post(f"/evidence/{image.json()['evidence_id']}/analyze", headers=supervisor_headers)).status_code == 400


async def test_standalone_analysis_stores_nothing(client, supervisor_headers, admin_headers, operator_headers, session, fake_worker, tmp_path):
    fake_worker.result = {"persons_found": [3], "vehicles_found": [], "animals_found": [], "timeline": [],
                          "alerts_fired": [{"alert_id": "ALT-20260918101013-dddddddddddddddd", "alert_type": "intrusion",
                                            "video_seconds": 1.0, "risk_score": 90, "risk_level": "critical"}]}
    content = mpeg4_part2_video(tmp_path / "s.mp4")
    files = {"file": ("s.mp4", content, "video/mp4")}
    assert (await client.post("/evidence/analyze-standalone", headers=operator_headers, files=files)).status_code == 403
    started = await client.post("/evidence/analyze-standalone", headers=supervisor_headers, files=files)
    assert started.status_code == 202, started.text
    assert started.json()["standalone"] is True
    job = await wait_for(client, supervisor_headers, started.json()["job_id"])
    assert job["status"] == "complete" and job["summary"]["persons_found"] == 1 and job["alerts_created"] == []
    session.expire_all()
    assert (await session.execute(select(Alert))).scalars().all() == []
    assert (await session.execute(select(Evidence))).scalars().all() == []
    assert not analysis_jobs.get(job["job_id"]).video_path or not Path(analysis_jobs.get(job["job_id"]).video_path).exists()
    # Another supervisor may not read somebody else's standalone job; an admin may.
    assert (await client.get(f"/evidence/analysis-jobs/{job['job_id']}", headers=admin_headers)).status_code == 200

    text = await client.post("/evidence/analyze-standalone", headers=supervisor_headers,
                             files={"file": ("notes.jpg", jpeg_bytes(), "image/jpeg")})
    assert text.status_code == 415


def test_high_risk_moments_merge_and_threshold():
    timeline = [{"video_seconds": s, "max_risk_score": r} for s, r in [(1, 70), (2, 85), (3, 40), (10, 90), (20, 61)]]
    assert high_risk_moments(timeline) == [
        {"start_seconds": 1.0, "end_seconds": 2.0, "peak_risk": 85},
        {"start_seconds": 10.0, "end_seconds": 10.0, "peak_risk": 90},
        {"start_seconds": 20.0, "end_seconds": 20.0, "peak_risk": 61},
    ]
