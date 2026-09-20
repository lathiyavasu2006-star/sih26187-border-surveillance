"""Clip encoding (browser playback, real frame rate) and the backend video-analysis worker."""
import argparse
import json
import sys

import cv2
import pytest

from ml.analysis_job import JobReporter, run
from ml.evidence import EvidenceCapture, clip_frame_rate
from ml.tests.conftest import shifted_frames, write_video


@pytest.fixture
def scene_video(tmp_path, bus_image):
    return write_video(tmp_path / "scene.mp4", shifted_frames(bus_image, 45, step_px=3), fps=15)


def _fourcc(path: str) -> str:
    capture = cv2.VideoCapture(path)
    try:
        code = int(capture.get(cv2.CAP_PROP_FOURCC))
        return "".join(chr((code >> 8 * i) & 0xFF) for i in range(4)).lower()
    finally:
        capture.release()


def test_clip_is_written_at_capture_rate_not_pipeline_rate(evidence_dirs, bus_image):
    capture = EvidenceCapture(buffer_seconds=2, fps=30)
    frames = shifted_frames(bus_image, 60, step_px=1)
    for i, frame in enumerate(frames[:30]):
        capture.add_frame(frame, timestamp=i / 30)
    # The pipeline was only processing 3 fps when the alert fired; frames were still buffered at 30 fps.
    job = capture.start_clip("ALT-RATE-0001", "CAM-N-001", 1, fps=3, duration_after=1, now=29 / 30)
    for i, frame in enumerate(frames[30:], start=30):
        capture.add_frame(frame, timestamp=i / 30)
    [due] = capture.due_clips(now=3.0)
    assert len(due.timestamps) == len(due.frames) == 60
    assert abs(clip_frame_rate(due) - 30.0) < 0.5

    path, _ = capture.write_clip(due)
    video = cv2.VideoCapture(path)
    fps = video.get(cv2.CAP_PROP_FPS)
    count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    video.release()
    assert abs(fps - 30.0) < 0.5
    assert abs(count / fps - 2.0) < 0.2, "a 2 s clip must play for 2 s"
    assert due.duration_seconds == 2


def test_clip_uses_a_browser_playable_codec_when_available(evidence_dirs, bus_image):
    capture = EvidenceCapture(buffer_seconds=1, fps=10)
    for i, frame in enumerate(shifted_frames(bus_image, 10)):
        capture.add_frame(frame, timestamp=i / 10)
    job = capture.start_clip("ALT-CODEC-0001", "CAM-N-001", 1, fps=10, duration_after=0, now=0.9)
    [due] = capture.due_clips(now=1.0)
    path, _ = capture.write_clip(due)
    codec = _fourcc(path)
    assert due.codec in ("avc1", "mp4v")
    if sys.platform == "win32":
        # Windows Media Foundation provides H.264, which Chrome/Edge can decode (MPEG-4 Part 2 they cannot).
        assert due.codec == "avc1" and codec in ("avc1", "h264")
    assert open(path, "rb").read(12)[4:8] == b"ftyp"


async def test_video_upload_reports_progress(evidence_dirs, fake_backend, backend_client, loaded_detector, scene_video):
    from ml.fence import FenceChecker
    from ml.video_upload import VideoUploadProcessor

    updates = []
    processor = VideoUploadProcessor(detector=loaded_detector, fence=FenceChecker(client=backend_client))
    result = await processor.process(str(scene_video), "CAM-N-001", frame_step=3, zones=[], on_progress=updates.append)
    assert updates, "progress must be reported while the video is processed"
    reads = [update["frames_read"] for update in updates]
    assert reads == sorted(reads) and reads[-1] == 45
    assert updates[-1]["persons"] == len(result["persons_found"])
    assert {"video_seconds", "vehicles", "animals", "alerts"} <= set(updates[-1])


def test_job_reporter_writes_atomically(tmp_path):
    job_file = tmp_path / "job.json"
    reporter = JobReporter(job_file, frames_total=200)
    reporter.write("loading")
    reporter.update({"frames_read": 50, "persons": 2})
    data = json.loads(job_file.read_text(encoding="utf-8"))
    assert data["status"] == "running"
    assert data["progress"]["percent"] == 25.0 and data["progress"]["persons"] == 2
    assert not job_file.with_suffix(".tmp").exists()


async def test_standalone_job_runs_end_to_end_and_stores_nothing(evidence_dirs, loaded_detector, scene_video, tmp_path):
    job_file = tmp_path / "standalone.json"
    args = argparse.Namespace(video=str(scene_video), job_file=str(job_file), camera=None, standalone=True,
                              frame_step=1, recorded_at=None)
    assert await run(args) == 0
    data = json.loads(job_file.read_text(encoding="utf-8"))
    assert data["status"] == "complete" and data["error"] is None
    assert data["progress"]["percent"] == 100.0 and data["progress"]["frames_read"] == 45
    assert len(data["result"]["persons_found"]) >= 3
    assert data["result"]["zones_used"] == 0
    assert data["result"]["evidence_saved"] == []
    assert list((evidence_dirs / "snapshots").iterdir()) == []
    frame = job_file.with_suffix(".jpg")
    assert frame.exists() and frame.read_bytes()[:2] == bytes([0xFF, 0xD8]), "live view frame is a JPEG"
    assert cv2.imread(str(frame)) is not None


async def test_job_reports_unreadable_video(evidence_dirs, loaded_detector, tmp_path):
    job_file = tmp_path / "bad.json"
    args = argparse.Namespace(video=str(tmp_path / "missing.mp4"), job_file=str(job_file), camera=None,
                              standalone=True, frame_step=1, recorded_at=None)
    assert await run(args) == 3
    data = json.loads(job_file.read_text(encoding="utf-8"))
    assert data["status"] == "failed" and "not found" in data["error"]


async def test_live_frames_are_throttled(evidence_dirs, fake_backend, backend_client, loaded_detector, scene_video):
    from ml.fence import FenceChecker
    from ml.video_upload import VideoUploadProcessor

    frames = []
    processor = VideoUploadProcessor(detector=loaded_detector, fence=FenceChecker(client=backend_client))
    every = await processor.process(str(scene_video), "CAM-N-001", frame_step=1, zones=[], on_frame=frames.append,
                                    frame_interval_seconds=0.0)
    assert len(frames) == every["frames_processed"] == 45
    assert frames[0].shape[:2] == (every["resolution"][1], every["resolution"][0])
    throttled = []
    await processor.process(str(scene_video), "CAM-N-001", frame_step=1, zones=[], on_frame=throttled.append,
                            frame_interval_seconds=3600)
    assert len(throttled) == 1, "at most one preview per interval"
