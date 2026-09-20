"""End-to-end pipeline against a fake backend (HTTP mock + real WebSocket server) and a recorded video.

Every message the pipeline publishes is validated with the backend's own `WSFrameData` schema, so a
contract break between the ML process and the Week 2 backend fails here instead of in production.
"""
import asyncio
import hashlib
from pathlib import Path

import numpy as np
import pytest

from ml.camera_stream import CameraStream
from ml.config import ml_config
from ml.fence import FenceChecker
from ml.pipeline import Pipeline
from ml.tests.conftest import DAY, AdvancingClock, shifted_frames, write_video
from ml.ws_client import WSClient

CAMERA = "CAM-N-001"


def full_frame_zone(width: int, height: int, zone_type="restricted", risk_bonus=50):
    return {"zone_id": 1, "zone_name": "Fence Line North", "zone_type": zone_type, "risk_bonus": risk_bonus,
            "polygon": [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], "is_active": True,
            "loiter_threshold_seconds": 30, "night_rules": {"multiplier": 1.5, "start": 22, "end": 5}}


@pytest.fixture
def scene_video(tmp_path, bus_image):
    return write_video(tmp_path / "scene.mp4", shifted_frames(bus_image, 45, step_px=3), fps=15)


@pytest.fixture
def fast_publishing(monkeypatch):
    # Publish every processed frame so short test videos produce enough messages.
    monkeypatch.setattr(ml_config, "ws_send_fps", 1000.0)
    monkeypatch.setattr(ml_config, "detection_publish_hz", 1000.0)


def build_pipeline(scene_video, backend_client, ws_server, loaded_detector, bus_image):
    height, width = bus_image.shape[:2]
    return Pipeline(
        CAMERA,
        str(scene_video),
        client=backend_client,
        stream=CameraStream(str(scene_video), CAMERA, realtime=False),
        ws_client=WSClient(CAMERA, client=backend_client, url_override=f"{ws_server}/ws/{CAMERA}"),
        detector=loaded_detector,
        fence=FenceChecker(client=backend_client),
        clock=AdvancingClock(DAY, 1 / 15),
    ), (width, height)


def validate_with_backend_schema(messages):
    from backend.schemas.api import WSFrameData

    return [WSFrameData.model_validate(message) for message in messages]


async def test_pipeline_end_to_end(evidence_dirs, fake_backend, backend_client, ws_server, loaded_detector,
                                   bus_image, scene_video, fast_publishing):
    pipeline, (width, height) = build_pipeline(scene_video, backend_client, ws_server, loaded_detector, bus_image)
    fake_backend.zones = [full_frame_zone(width, height)]

    await asyncio.wait_for(pipeline.run(), timeout=120)

    assert pipeline.frames_processed == 45
    assert fake_backend.logins == 1, "the whole pipeline shares one authenticated session"
    assert fake_backend.ws_connections == 1

    frame_messages = [m for m in fake_backend.ws_messages if m["frame"]]
    alert_messages = [m for m in fake_backend.ws_messages if m["alerts"]]
    assert len(frame_messages) >= 40
    validated = validate_with_backend_schema(fake_backend.ws_messages)
    assert len(validated) == len(fake_backend.ws_messages)

    # Detections: backend field names, persistent ids, zone applied.
    with_people = [m for m in frame_messages if any(d["object_class"] == "person" for d in m["detections"])]
    assert len(with_people) >= 30
    first_ids = {d["track_id"] for d in with_people[2]["detections"] if d["object_class"] == "person"}
    last_ids = {d["track_id"] for d in with_people[-1]["detections"] if d["object_class"] == "person"}
    assert len(first_ids & last_ids) >= 2, "people must keep their track ids through the clip"
    sample = with_people[-1]["detections"][0]
    assert sample["in_fence"] is True and sample["zone_type"] == "restricted"
    assert sample["risk_score"] >= 60 and sample["risk_level"] in {"suspicious", "high_risk", "critical"}
    assert with_people[-1]["stats"]["people_count"] >= 3

    # Alerts: one per (track, type) inside the 15 s cooldown, each with a hashed snapshot on disk.
    alerts = [alert for message in alert_messages for alert in message["alerts"]]
    assert alerts, "people inside a restricted zone must raise alerts"
    keys = [(a["track_id"], a["alert_type"]) for a in alerts]
    assert len(keys) == len(set(keys)), "cooldown must suppress repeat alerts for the same track and type"
    assert {a["alert_type"] for a in alerts} <= {"intrusion", "vehicle"}
    for message in alert_messages:
        assert message["frame"] is None and message["detections"] == []
    for alert in alerts:
        record = next(r for r in pipeline.alert_records if r["alert_id"] == alert["alert_id"])
        snapshot = Path(alert["snapshot_path"])
        assert snapshot.is_file() and snapshot.parent == evidence_dirs / "snapshots"
        assert record["snapshot_sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
        assert alert["zone_type"] == "restricted" and alert["zone_name"] == "Fence Line North"

    # Clips: finalised on shutdown, uploaded, backend hash equals local hash, local copy removed.
    assert pipeline.clip_records, "alert clips must be finalised when the pipeline stops"
    for record in pipeline.clip_records:
        assert record["uploaded"] is True, record
        assert not Path(record["file_path"]).exists()
    assert {u["sha256"] for u in fake_backend.uploads} == {r["sha256"] for r in pipeline.clip_records}

    manifest = (evidence_dirs / "ml_evidence_manifest.jsonl").read_text().splitlines()
    assert len(manifest) == len(alerts) + len(pipeline.clip_records)


async def test_no_zones_means_no_intrusion_alerts(evidence_dirs, fake_backend, backend_client, ws_server,
                                                   loaded_detector, bus_image, scene_video, fast_publishing):
    pipeline, _ = build_pipeline(scene_video, backend_client, ws_server, loaded_detector, bus_image)
    fake_backend.zones = []
    await asyncio.wait_for(pipeline.run(max_frames=20), timeout=120)

    assert pipeline.frames_processed == 20
    assert not [m for m in fake_backend.ws_messages if m["alerts"]]
    detections = [d for m in fake_backend.ws_messages for d in m["detections"]]
    assert detections and all(d["risk_score"] == 10 and d["risk_level"] == "normal" for d in detections)
    assert all(d["in_fence"] is False and d["zone_type"] is None for d in detections)


async def test_websocket_reconnects_and_resends_alerts(evidence_dirs, fake_backend, backend_client, ws_server,
                                                       loaded_detector, bus_image, scene_video, fast_publishing):
    pipeline, (width, height) = build_pipeline(scene_video, backend_client, ws_server, loaded_detector, bus_image)
    pipeline.ws_client._reconnect_delay = 0.1
    fake_backend.zones = [full_frame_zone(width, height)]
    fake_backend.close_first_connection_after = 3  # server drops the socket mid-stream

    await asyncio.wait_for(pipeline.run(), timeout=120)

    assert fake_backend.ws_connections >= 2, "client must reconnect after the server closes the socket"
    assert pipeline.ws_client.reconnects >= 1
    assert len(fake_backend.ws_messages) > 3, "publishing continues after reconnection"
    sent_alert_ids = {a["alert_id"] for m in fake_backend.ws_messages for a in m["alerts"]}
    produced_alert_ids = {r["alert_id"] for r in pipeline.alert_records}
    assert produced_alert_ids <= sent_alert_ids, "alerts raised while disconnected are re-sent, never lost"


class FlakyStream:
    """A camera that drops out for a while and comes back."""

    is_file = False
    ended = False
    fps = 15.0

    def __init__(self, frames, outage_reads=8):
        self.frames = list(frames)
        self.outage_reads = outage_reads
        self.reads = 0
        self.released = False

    def start(self):
        return True

    def read(self, timeout=1.0):
        self.reads += 1
        if 5 <= self.reads < 5 + self.outage_reads:
            return None  # stream lost
        return self.frames.pop(0) if self.frames else None

    def is_alive(self):
        return True

    def release(self):
        self.released = True


async def test_pipeline_survives_camera_dropout(evidence_dirs, fake_backend, backend_client, ws_server,
                                                loaded_detector, bus_image, fast_publishing):
    stream = FlakyStream(shifted_frames(bus_image, 20))
    pipeline = Pipeline(CAMERA, "flaky", client=backend_client, stream=stream,
                        ws_client=WSClient(CAMERA, client=backend_client, url_override=f"{ws_server}/ws/{CAMERA}"),
                        detector=loaded_detector, fence=FenceChecker(client=backend_client),
                        clock=AdvancingClock(DAY, 1 / 15))
    await asyncio.wait_for(pipeline.run(max_frames=20), timeout=120)
    assert pipeline.frames_processed == 20, "processing resumes after the outage"
    assert stream.reads >= 28 and stream.released


def test_camera_stream_reconnects_after_failures(monkeypatch):
    stream = CameraStream("rtsp://127.0.0.1:1/unreachable", "CAM-RECON", realtime=False)
    stream._reconnect_delay = 0.01
    stream._max_reconnect_delay = 0.01
    outcomes = iter([False, False, True])
    monkeypatch.setattr(stream, "_connect", lambda: next(outcomes))

    assert stream._attempt_reconnect() is False
    assert stream._attempt_reconnect() is False
    assert stream._attempt_reconnect() is True
    assert stream.reconnects == 1 and stream._reconnect_attempts == 3


def test_camera_stream_backoff_is_capped():
    stream = CameraStream("rtsp://127.0.0.1:1/x", "CAM-BACKOFF")
    delays = []
    for attempt in range(8):
        stream._reconnect_attempts = attempt
        delays.append(stream._next_backoff())
    assert delays[0] == 5 and delays == sorted(delays) and max(delays) == 30


def test_camera_stream_reads_file_sequentially(scene_video):
    stream = CameraStream(str(scene_video), "CAM-FILE")
    assert stream.is_file and not stream.realtime
    assert stream.start() is True
    frames = 0
    while stream.read() is not None:
        frames += 1
    assert frames == 45 and stream.ended
    stream.release()


async def test_video_upload_processor(evidence_dirs, fake_backend, backend_client, loaded_detector, bus_image, scene_video):
    from ml.video_upload import VideoUploadProcessor

    height, width = bus_image.shape[:2]
    processor = VideoUploadProcessor(detector=loaded_detector, fence=FenceChecker(client=backend_client))
    fake_backend.zones = [full_frame_zone(width, height, "no_mans_land", 100)]
    result = await processor.process(str(scene_video), CAMERA, frame_step=1, recorded_at=DAY)

    assert result["frames_read"] == 45 and result["frames_processed"] == 45
    assert len(result["persons_found"]) >= 3
    assert result["zones_used"] == 1
    assert result["alerts_fired"] and all(a["risk_level"] == "critical" for a in result["alerts_fired"])
    assert result["timeline"] and result["timeline"][-1]["video_seconds"] >= 2.5
    for item in result["evidence_saved"]:
        assert hashlib.sha256(Path(item["file_path"]).read_bytes()).hexdigest() == item["sha256"]

    missing = await processor.process(str(evidence_dirs / "missing.mp4"), CAMERA)
    assert "error" in missing


def test_camera_exposure_setting(monkeypatch):
    from ml.config import MLConfig

    monkeypatch.setenv("ML_CAMERA_EXPOSURE", "-6")
    assert MLConfig().camera_exposure == -6.0
    monkeypatch.setenv("ML_CAMERA_EXPOSURE", "auto")
    assert MLConfig().camera_exposure is None
    monkeypatch.delenv("ML_CAMERA_EXPOSURE")
    assert MLConfig().camera_exposure is None, "auto exposure is the default: evidence must not be darkened silently"


async def test_publishing_never_blocks_detection(evidence_dirs, fake_backend, backend_client, ws_server,
                                                 loaded_detector, bus_image, scene_video, fast_publishing):
    pipeline, _ = build_pipeline(scene_video, backend_client, ws_server, loaded_detector, bus_image)
    fake_backend.zones = []
    release = asyncio.Event()
    original = pipeline._publish_now

    async def slow_publish(*args, **kwargs):
        await release.wait()           # a stalled network send
        return await original(*args, **kwargs)

    pipeline._publish_now = slow_publish
    await pipeline.fence.load_zones(CAMERA)
    for frame in shifted_frames(bus_image, 10):
        await asyncio.wait_for(pipeline.process_frame(frame), timeout=10)
    assert pipeline.frames_processed == 10, "detection keeps running while a publish is stuck"
    assert pipeline.frames_skipped_publish == 9, "newer frames are skipped instead of queueing behind the stall"
    release.set()
    await pipeline.shutdown()
