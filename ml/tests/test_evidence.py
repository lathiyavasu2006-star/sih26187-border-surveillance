"""Evidence: SHA-256, snapshots, compressed pre-alert buffer, clips and verified upload."""
import hashlib
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from ml.evidence import EvidenceCapture, compute_sha256
from ml.tests.conftest import shifted_frames


def test_sha256_matches_hashlib(tmp_path):
    path = tmp_path / "data.bin"
    payload = np.random.default_rng(1).bytes(3 * 1024 * 1024 + 17)  # spans several read chunks
    path.write_bytes(payload)
    assert compute_sha256(str(path)) == hashlib.sha256(payload).hexdigest()


async def test_snapshot_is_written_and_hashed_immediately(evidence_dirs, bus_image):
    capture = EvidenceCapture()
    path, digest = await capture.capture_snapshot("ALT-20260917120000-abcdef0123456789", "CAM-N-001", bus_image)

    file_path = Path(path)
    assert file_path.is_file()
    assert file_path.parent == evidence_dirs / "snapshots"
    assert file_path.name == "ALT-20260917120000-abcdef0123456789.jpg"
    assert digest == hashlib.sha256(file_path.read_bytes()).hexdigest()
    assert file_path.read_bytes()[:3] == b"\xff\xd8\xff"
    assert cv2.imread(path).shape == bus_image.shape


async def test_snapshot_never_overwrites_evidence(evidence_dirs, bus_image):
    capture = EvidenceCapture()
    await capture.capture_snapshot("ALT-DUPLICATE-0001", "CAM-N-001", bus_image)
    with pytest.raises(FileExistsError):
        await capture.capture_snapshot("ALT-DUPLICATE-0001", "CAM-N-001", bus_image)


async def test_tampering_is_detectable(evidence_dirs, bus_image):
    capture = EvidenceCapture()
    path, digest = await capture.capture_snapshot("ALT-TAMPER-0001", "CAM-N-001", bus_image)
    data = bytearray(Path(path).read_bytes())
    data[len(data) // 2] ^= 0xFF
    Path(path).write_bytes(bytes(data))
    assert capture.compute_sha256(path) != digest


def test_frame_buffer_is_compressed_and_bounded(bus_image):
    capture = EvidenceCapture(buffer_seconds=2, fps=10)
    for i in range(50):
        capture.add_frame(bus_image, timestamp=i * 0.1)
    assert len(capture.frame_buffer) == 20
    raw_bytes = bus_image.nbytes * 20
    stored = sum(len(data) for _, data in capture.frame_buffer)
    assert stored < raw_bytes / 5, "buffered frames must be JPEG-compressed"
    assert capture.buffered_seconds() == pytest.approx(1.9)


def test_clip_includes_pre_and_post_alert_frames(evidence_dirs, bus_image):
    capture = EvidenceCapture(buffer_seconds=2, fps=10)
    frames = shifted_frames(bus_image, 40, step_px=2)
    for i, frame in enumerate(frames[:20]):
        capture.add_frame(frame, timestamp=i / 10)

    job = capture.start_clip("ALT-CLIP-0001", "CAM-N-001", 3, fps=10, duration_after=1, now=19 / 10)
    assert capture.active_clip_count() == 1
    for i, frame in enumerate(frames[20:40], start=20):
        capture.add_frame(frame, timestamp=i / 10)
    assert capture.due_clips(now=2.5) == []
    due = capture.due_clips(now=3.0)
    assert due == [job] and capture.active_clip_count() == 0

    assert len(job.frames) == 20 + 10  # 2 s before + 1 s after (frames up to t=2.9)
    path, digest = capture.write_clip(job)
    data = Path(path).read_bytes()
    assert data[4:8] == b"ftyp", "must be an MP4 the backend upload endpoint accepts"
    assert digest == hashlib.sha256(data).hexdigest()
    assert Path(path + ".sha256").read_text().startswith(digest)
    assert job.duration_seconds == 3 and job.frames == []

    video = cv2.VideoCapture(path)
    assert int(video.get(cv2.CAP_PROP_FRAME_COUNT)) == 30
    video.release()


class FakeUploadClient:
    def __init__(self, tamper=False, fail=False):
        self.tamper, self.fail, self.calls = tamper, fail, []

    async def upload_evidence(self, file_path, camera_id, alert_id=None, track_id=None):
        self.calls.append((file_path, camera_id, alert_id, track_id))
        if self.fail:
            raise ConnectionError("backend unreachable")
        digest = hashlib.sha256(Path(file_path).read_bytes()).hexdigest()
        return {"evidence_id": 42, "file_hash": ("0" * 64) if self.tamper else digest}


def make_job(capture, bus_image, alert_id):
    for i, frame in enumerate(shifted_frames(bus_image, 12)):
        capture.add_frame(frame, timestamp=i * 0.1)
    return capture.start_clip(alert_id, "CAM-N-001", 5, fps=10, duration_after=0, now=1.1)


async def test_upload_verified_then_local_copy_removed(evidence_dirs, bus_image):
    capture = EvidenceCapture(buffer_seconds=2, fps=10)
    job = make_job(capture, bus_image, "ALT-UPLOAD-0001")
    client = FakeUploadClient()
    record = await capture.finalize_and_upload(job, client)

    assert record["uploaded"] is True and record["evidence_id"] == 42
    assert client.calls[0][1:] == ("CAM-N-001", "ALT-UPLOAD-0001", 5)
    assert not Path(record["file_path"]).exists(), "verified upload leaves no duplicate on disk"
    assert job.finished


async def test_hash_mismatch_keeps_local_evidence(evidence_dirs, bus_image):
    capture = EvidenceCapture(buffer_seconds=2, fps=10)
    job = make_job(capture, bus_image, "ALT-UPLOAD-0002")
    record = await capture.finalize_and_upload(job, FakeUploadClient(tamper=True))
    assert record["uploaded"] is False and record["error"] == "hash_mismatch"
    assert Path(record["file_path"]).is_file()
    assert compute_sha256(record["file_path"]) == record["sha256"]


async def test_backend_down_keeps_clip_with_sidecar(evidence_dirs, bus_image):
    capture = EvidenceCapture(buffer_seconds=2, fps=10)
    job = make_job(capture, bus_image, "ALT-UPLOAD-0003")
    record = await capture.finalize_and_upload(job, FakeUploadClient(fail=True))
    assert record["uploaded"] is False and "ConnectionError" in record["error"]
    assert Path(record["file_path"]).is_file() and Path(record["file_path"] + ".sha256").is_file()


def test_manifest_is_append_only_jsonl(evidence_dirs):
    import json

    capture = EvidenceCapture()
    capture.append_manifest({"alert_id": "A1", "sha256": "a" * 64})
    capture.append_manifest({"alert_id": "A2", "sha256": "b" * 64})
    lines = (evidence_dirs / "ml_evidence_manifest.jsonl").read_text().strip().splitlines()
    assert [json.loads(line)["alert_id"] for line in lines] == ["A1", "A2"]
    assert all("recorded_at" in json.loads(line) for line in lines)
