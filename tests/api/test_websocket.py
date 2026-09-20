"""WebSocket stream: authentication, ping/pong, ML frame ingestion, acknowledgement, broadcasts."""
import base64
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from starlette.testclient import TestClient

from backend.core.auth import create_access_token, token_claims_for
from backend.core.enums import CameraStatus
from backend.models import Alert, AuditLog, Camera, Event, Evidence, TrackedObject

from tests.api.conftest import jpeg_bytes


@pytest.fixture
def ws_client():
    from backend.main import app

    # The context manager keeps ONE event loop portal for every connection made through this client.
    # Without it Starlette gives each websocket_connect its own loop, and a broadcast produced on one
    # loop can never reach a socket living on another. The camera monitor is disabled in conftest.
    with TestClient(app) as client:
        yield client


def token_for(user) -> str:
    return create_access_token(token_claims_for(user))


def frame_message(camera_id="CAM-N-001", with_alert=True, risk_score=85, include_frame=True):
    message = {
        "type": "frame_data",
        "camera_id": camera_id,
        "detections": [{
            "track_id": 7, "object_class": "person", "confidence": 0.91,
            "cx": 320, "cy": 240, "bbox_x1": 300, "bbox_y1": 200, "bbox_x2": 340, "bbox_y2": 280,
            "in_fence": True, "zone_name": "Fence Line North", "zone_type": "restricted",
            "loitering": False, "time_in_zone_seconds": 4, "direction": "north",
            "risk_score": risk_score, "risk_level": "critical" if risk_score > 80 else "suspicious",
        }],
        "alerts": [],
        "stats": {"people_count": 1, "vehicle_count": 0, "animal_count": 0, "active_alerts": 1, "fps": 15.2},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if with_alert:
        message["alerts"] = [{
            "alert_type": "intrusion", "risk_score": risk_score,
            "risk_reasons": ["restricted zone", "night hours"], "track_id": 7,
            "zone_name": "Fence Line North", "zone_type": "restricted",
        }]
    if include_frame:
        message["frame"] = base64.b64encode(jpeg_bytes()).decode("ascii")
    return message


def test_rejects_missing_and_invalid_token(ws_client, ws_camera):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/ws/CAM-N-001") as ws:
            ws.receive_json()

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect("/ws/CAM-N-001?token=not-a-jwt") as ws:
            ws.receive_json()


def test_rejects_unknown_camera_and_unauthorised_user(ws_client, ws_camera, ws_admin, ws_outsider):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(f"/ws/CAM-N-404?token={token_for(ws_admin)}") as ws:
            ws.receive_json()

    with pytest.raises(WebSocketDisconnect):
        with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_outsider)}") as ws:
            ws.receive_json()


def test_connect_ping_pong_and_audit(ws_client, ws_camera, ws_operator):
    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_operator)}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "connected"
        assert hello["camera_id"] == "CAM-N-001"
        assert hello["user_id"] == str(ws_operator.user_id)
        assert hello["may_ingest_frames"] is False     # operators may not push frames
        assert hello["viewer_count"] == 1

        ws.send_json({"type": "ping"})
        pong = ws.receive_json()
        assert pong["type"] == "pong" and pong["timestamp"]

        ws.send_json({"type": "nonsense"})
        assert "Unknown message type" in ws.receive_json()["message"]

        ws.send_text("{not json")
        assert ws.receive_json()["type"] == "error"


def test_operator_cannot_publish_frames(ws_client, ws_camera, ws_operator):
    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_operator)}") as ws:
        ws.receive_json()
        ws.send_json(frame_message())
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "may not publish frame_data" in error["message"]


def test_frame_data_persists_detections_alerts_and_evidence(ws_client, ws_camera, ws_supervisor, ws_admin, sync_db):
    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_supervisor)}") as pipeline:
        pipeline.receive_json()
        with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_admin)}") as viewer:
            viewer.receive_json()

            pipeline.send_json(frame_message())

            # The critical alarm and the frame update are independent broadcasts; order is not guaranteed.
            received = {}
            while {"frame_update", "critical_alert"} - set(received):
                message = viewer.receive_json()
                received[message["type"]] = message

            update = received["frame_update"]
            assert update["camera_id"] == "CAM-N-001"
            assert update["viewer_count"] == 2
            assert update["server_timestamp"]
            assert len(update["detections"]) == 1
            assert len(update["alerts"]) == 1

            critical = received["critical_alert"]
            assert critical["severity"] == "critical"
            assert critical["risk_score"] == 85
            assert critical["sound"] == "alarm"

            ack = pipeline.receive_json()
            # the pipeline receives its own broadcasts as well as the frame_ack
            while ack["type"] != "frame_ack":
                ack = pipeline.receive_json()
            assert ack["detections_saved"] == 1
            assert ack["alerts_created"] == 1
            assert ack["problems"] == []

    rows = sync_db("SELECT track_id, cx, cy, risk_level, zone_type FROM tracked_objects")
    assert rows == [(7, 320, 240, "critical", "restricted")]

    alerts = sync_db("SELECT alert_id, risk_level, snapshot_path, track_id FROM alerts")
    assert len(alerts) == 1 and alerts[0][1] == "critical" and alerts[0][2] and alerts[0][3] == 7

    evidence = sync_db("SELECT file_hash, evidence_type, alert_id FROM evidence")
    assert len(evidence) == 1
    assert len(evidence[0][0]) == 64 and evidence[0][1] == "snapshot"
    assert evidence[0][2] == alerts[0][0]

    events = sync_db("SELECT track_id, alert_count, max_risk_score, is_active FROM events")
    assert events == [(7, 1, 85, True)]

    camera_row = sync_db("SELECT status FROM cameras WHERE camera_id='CAM-N-001'")
    assert camera_row[0][0] == "online"

    audit = sync_db("SELECT action FROM audit_logs WHERE action IN ('WS_CONNECT','ALERT_CREATED','WS_DISCONNECT')")
    actions = {row[0] for row in audit}
    assert {"WS_CONNECT", "ALERT_CREATED"} <= actions


def test_duplicate_alert_id_is_ignored(ws_client, ws_camera, ws_supervisor, sync_db):
    message = frame_message()
    message["alerts"][0]["alert_id"] = "ALT-20260916120000-abcdef0123456789"

    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_supervisor)}") as ws:
        ws.receive_json()
        for _ in range(2):
            ws.send_json(message)
            reply = ws.receive_json()
            while reply["type"] != "frame_ack":
                reply = ws.receive_json()

    assert len(sync_db("SELECT alert_id FROM alerts")) == 1
    assert len(sync_db("SELECT evidence_id FROM evidence")) == 1


def test_invalid_frame_payload_is_reported(ws_client, ws_camera, ws_supervisor, sync_db):
    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_supervisor)}") as ws:
        ws.receive_json()

        bad = frame_message(with_alert=False)
        bad["detections"][0]["risk_score"] = 500
        ws.send_json(bad)
        error = ws.receive_json()
        assert error["type"] == "error" and error["errors"]

        mismatch = frame_message(camera_id="CAM-S-001", with_alert=False)
        ws.send_json(mismatch)
        assert "camera_id does not match" in ws.receive_json()["message"]

        not_jpeg = frame_message(with_alert=True, include_frame=False)
        not_jpeg["frame"] = base64.b64encode(b"not an image").decode("ascii")
        ws.send_json(not_jpeg)
        assert "JPEG" in ws.receive_json()["message"]

    assert sync_db("SELECT * FROM tracked_objects") == []


def test_acknowledge_over_websocket(ws_client, ws_camera, ws_supervisor, ws_operator, sync_db):
    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_supervisor)}") as pipeline:
        pipeline.receive_json()
        pipeline.send_json(frame_message())
        reply = pipeline.receive_json()
        while reply["type"] != "frame_ack":
            reply = pipeline.receive_json()

    alert_id = sync_db("SELECT alert_id FROM alerts")[0][0]

    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_operator)}") as ws:
        ws.receive_json()
        ws.send_json({"type": "acknowledge", "alert_id": alert_id, "false_alarm": False, "notes": "Patrol sent"})

        messages = [ws.receive_json(), ws.receive_json()]
        types = {message["type"] for message in messages}
        assert {"acknowledge_ok", "alert_acknowledged"} == types
        confirmation = next(m for m in messages if m["type"] == "acknowledge_ok")
        assert confirmation["alert"]["acknowledged"] is True

        ws.send_json({"type": "acknowledge", "alert_id": alert_id})
        assert "already acknowledged" in ws.receive_json()["message"]

    row = sync_db("SELECT acknowledged, acknowledged_by, notes FROM alerts")[0]
    assert row[0] is True and str(row[1]) == str(ws_operator.user_id) and row[2] == "Patrol sent"


def test_camera_offline_broadcast_reaches_operators(ws_client, ws_camera, ws_admin, ws_operator):
    """An operator watching one camera still receives system-wide notifications."""
    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_operator)}") as ws:
        ws.receive_json()
        response = ws_client.patch(
            "/cameras/CAM-N-001/status",
            headers={"Authorization": f"Bearer {token_for(ws_admin)}"},
            json={"status": "offline"},
        )
        assert response.status_code == 200
        notification = ws.receive_json()
        assert notification["type"] == "camera_offline"
        assert notification["severity"] == "warning"
        assert notification["camera_id"] == "CAM-N-001"
        assert "OFFLINE" in notification["message"]


def test_viewer_count_tracks_connections(ws_client, ws_camera, ws_admin, ws_operator):
    from backend.websocket.manager import manager

    with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_admin)}") as first:
        first.receive_json()
        assert manager.get_camera_viewer_count("CAM-N-001") == 1
        with ws_client.websocket_connect(f"/ws/CAM-N-001?token={token_for(ws_operator)}") as second:
            second.receive_json()
            assert manager.get_camera_viewer_count("CAM-N-001") == 2
            assert manager.get_connected_count() == 2
    assert manager.get_camera_viewer_count("CAM-N-001") == 0
    assert manager.get_connection_count() == 0


# --------------------------------------------------------------------------- /ws/analysis/{job_id}

def _finished_job(analysis_dir, camera_id=None, owner=None):
    """A completed analysis job as the manager holds it, with the worker's last annotated frame on disk."""
    import uuid

    from backend.services.analysis_jobs import AnalysisJob, analysis_jobs

    job = AnalysisJob(
        job_id=uuid.uuid4().hex, video_path="unused.mp4", camera_id=camera_id, evidence_id=None,
        user_id=getattr(owner, "user_id", None), username=getattr(owner, "username", "x"),
        recorded_at=datetime.now(timezone.utc),
    )
    job.status = "complete"
    job.finished_at = datetime.now(timezone.utc)
    job.progress = {"percent": 100.0, "frames_read": 45, "frames_total": 45, "persons": 2, "vehicles": 1}
    job.summary = {"persons_found": 2, "vehicles_found": 1, "animals_found": 0, "alerts_detected": 0,
                   "alerts_created": 0, "evidence_saved": 0, "high_risk_moments": []}
    analysis_dir.mkdir(parents=True, exist_ok=True)
    job.frame_file.write_bytes(jpeg_bytes())
    analysis_jobs.jobs[job.job_id] = job
    return job


def test_analysis_stream_pushes_frame_progress_and_summary(ws_client, ws_supervisor, api_environment):
    from backend.services.analysis_jobs import analysis_dir, analysis_jobs

    job = _finished_job(analysis_dir(), owner=ws_supervisor)
    try:
        with ws_client.websocket_connect(f"/ws/analysis/{job.job_id}?token={token_for(ws_supervisor)}") as ws:
            messages = []
            while True:
                message = ws.receive_json()
                messages.append(message)
                if message["type"] == "analysis_complete":
                    break
        kinds = [message["type"] for message in messages]
        assert kinds[0] == "analysis_frame" and "analysis_progress" in kinds
        frame = next(message for message in messages if message["type"] == "analysis_frame")
        assert base64.b64decode(frame["frame"]) == job.frame_file.read_bytes()
        final = messages[-1]["job"]
        assert final["status"] == "complete" and final["persons"] == 2 and final["summary"]["vehicles_found"] == 1
    finally:
        analysis_jobs.jobs.pop(job.job_id, None)


def test_analysis_stream_access_control(ws_client, ws_supervisor, ws_operator, ws_admin, api_environment, sync_db):
    from starlette.websockets import WebSocketDisconnect

    from backend.services.analysis_jobs import analysis_dir, analysis_jobs

    job = _finished_job(analysis_dir(), owner=ws_supervisor)
    try:
        for url in (
            f"/ws/analysis/{job.job_id}",                                   # no token
            f"/ws/analysis/{job.job_id}?token=not-a-jwt",                   # bad token
            f"/ws/analysis/doesnotexist?token={token_for(ws_admin)}",       # unknown job
            f"/ws/analysis/{job.job_id}?token={token_for(ws_operator)}",    # someone else's standalone job
        ):
            with pytest.raises(WebSocketDisconnect):
                with ws_client.websocket_connect(url) as ws:
                    ws.receive_json()
        # An administrator may watch any job.
        with ws_client.websocket_connect(f"/ws/analysis/{job.job_id}?token={token_for(ws_admin)}") as ws:
            assert ws.receive_json()["type"] in ("analysis_frame", "analysis_progress")
        denied = sync_db("SELECT count(*) FROM audit_logs WHERE action = 'WS_ANALYSIS_DENIED'")
        assert denied[0][0] == 4
    finally:
        analysis_jobs.jobs.pop(job.job_id, None)
