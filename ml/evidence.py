"""Evidence capture: SHA-256 snapshots and non-blocking pre/post-alert video clips.

* Snapshots are written as `{alert_id}.jpg` in the evidence snapshots folder and hashed immediately.
  The backend registers them when the alert arrives (it re-hashes the same file on its side).
* The pre-alert buffer holds JPEG-compressed frames: 15 s of raw 1080p frames would need ~2.7 GB of RAM
  per camera, compressed they need ~60 MB.
* Clips are recorded without blocking the detection loop: frames are appended as the pipeline produces
  them and the clip is finalised in a worker thread, hashed, uploaded, and the hash returned by the backend
  is compared with the local one (an in-transit integrity check). A clip that cannot be uploaded stays on
  disk with a `.sha256` sidecar for later submission.
"""
import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ml.config import ml_config

logger = logging.getLogger("sih26187.ml.evidence")

HASH_CHUNK = 1024 * 1024


def compute_sha256(file_path: str) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class ClipJob:
    alert_id: str
    camera_id: str
    track_id: int
    started_at: float
    end_at: float
    fps: float
    frames: List[bytes] = field(default_factory=list)
    # Capture time of each frame (monotonic seconds); used to write the clip at its real frame rate.
    timestamps: List[float] = field(default_factory=list)
    file_path: Optional[str] = None
    file_hash: Optional[str] = None
    duration_seconds: int = 0
    codec: str = ""
    finished: bool = False


def clip_frame_rate(job: "ClipJob") -> float:
    """Real capture rate of the buffered frames. The pipeline's processing fps can be far lower than the
    rate frames were buffered at, and writing at that rate stretched a 20 s clip to minutes."""
    stamps = job.timestamps
    if len(stamps) == len(job.frames) and len(stamps) >= 2 and stamps[-1] > stamps[0]:
        return float(min(60.0, max(1.0, (len(stamps) - 1) / (stamps[-1] - stamps[0]))))
    return max(1.0, float(job.fps))


def open_clip_writer(file_path: Path, fps: float, size: Tuple[int, int]):
    """H.264 (browser-playable) via Media Foundation or FFmpeg when available, else MPEG-4 Part 2."""
    attempts = [(getattr(cv2, "CAP_MSMF", None), "avc1"), (getattr(cv2, "CAP_FFMPEG", None), "avc1"),
                (getattr(cv2, "CAP_FFMPEG", None), "mp4v")]
    for api, codec in attempts:
        if api is None:
            continue
        writer = cv2.VideoWriter(str(file_path), api, cv2.VideoWriter_fourcc(*codec), fps, size)
        if writer.isOpened():
            return writer, codec
        writer.release()
        file_path.unlink(missing_ok=True)
    raise RuntimeError("VideoWriter could not be opened")


class EvidenceCapture:
    def __init__(self, buffer_seconds: Optional[int] = None, fps: float = 30.0):
        self.buffer_seconds = buffer_seconds or ml_config.frame_buffer_seconds
        maxlen = max(1, int(self.buffer_seconds * max(fps, 1.0)))
        # (monotonic timestamp, jpeg bytes)
        self.frame_buffer: Deque[Tuple[float, bytes]] = deque(maxlen=min(maxlen, ml_config.frame_buffer_size))
        self._active_clips: Dict[str, ClipJob] = {}
        self._lock = threading.Lock()
        self._token: Optional[str] = None
        self.completed: List[ClipJob] = []

    # ------------------------------------------------------------------ buffering

    def add_frame(self, frame: np.ndarray, timestamp: Optional[float] = None) -> None:
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, ml_config.buffer_jpeg_quality])
        if not ok:
            return
        encoded = buffer.tobytes()
        now = time.monotonic() if timestamp is None else timestamp
        with self._lock:
            self.frame_buffer.append((now, encoded))
            for job in self._active_clips.values():
                if now <= job.end_at:
                    job.frames.append(encoded)
                    job.timestamps.append(now)

    def buffered_seconds(self) -> float:
        with self._lock:
            if len(self.frame_buffer) < 2:
                return 0.0
            return self.frame_buffer[-1][0] - self.frame_buffer[0][0]

    # ------------------------------------------------------------------ hashing / snapshots

    def compute_sha256(self, file_path: str) -> str:
        return compute_sha256(file_path)

    async def capture_snapshot(self, alert_id: str, camera_id: str, annotated_frame: np.ndarray) -> Tuple[str, str]:
        """Write `{alert_id}.jpg` and hash it immediately. Returns (posix path, sha256)."""
        directory = Path(ml_config.snapshots_path)
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"{alert_id}.jpg"

        def _write() -> str:
            ok, buffer = cv2.imencode(".jpg", annotated_frame, [cv2.IMWRITE_JPEG_QUALITY, ml_config.snapshot_jpeg_quality])
            if not ok:
                raise ValueError("JPEG encoding failed")
            data = buffer.tobytes()
            with open(file_path, "xb") as handle:  # never overwrite existing evidence
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            return hashlib.sha256(data).hexdigest()

        file_hash = await asyncio.to_thread(_write)
        return file_path.resolve().as_posix(), file_hash

    # ------------------------------------------------------------------ clips (non-blocking)

    def start_clip(self, alert_id: str, camera_id: str, track_id: int, fps: float,
                   duration_after: Optional[int] = None, now: Optional[float] = None) -> ClipJob:
        now = time.monotonic() if now is None else now
        duration_after = ml_config.clip_duration_after_alert if duration_after is None else duration_after
        with self._lock:
            pre_frames = [data for _, data in self.frame_buffer]
            pre_times = [stamp for stamp, _ in self.frame_buffer]
            job = ClipJob(alert_id=alert_id, camera_id=camera_id, track_id=track_id, started_at=now,
                          end_at=now + duration_after, fps=max(1.0, fps), frames=pre_frames, timestamps=pre_times)
            self._active_clips[alert_id] = job
        return job

    def due_clips(self, now: Optional[float] = None) -> List[ClipJob]:
        now = time.monotonic() if now is None else now
        with self._lock:
            due = [job for job in self._active_clips.values() if now >= job.end_at]
            for job in due:
                del self._active_clips[job.alert_id]
        return due

    def active_clip_count(self) -> int:
        with self._lock:
            return len(self._active_clips)

    def write_clip(self, job: ClipJob, directory: Optional[str] = None) -> Tuple[str, str]:
        """Encode buffered JPEG frames to MP4 and hash it. Blocking: run in a thread."""
        if not job.frames:
            raise ValueError(f"Clip {job.alert_id} has no frames")
        directory = Path(directory or ml_config.clips_path)
        directory.mkdir(parents=True, exist_ok=True)
        file_path = directory / f"{job.alert_id}.mp4"
        if file_path.exists():
            raise FileExistsError(f"Clip already exists: {file_path.name}")

        first = cv2.imdecode(np.frombuffer(job.frames[0], np.uint8), cv2.IMREAD_COLOR)
        height, width = first.shape[:2]
        fps = clip_frame_rate(job)
        writer, job.codec = open_clip_writer(file_path, fps, (width, height))
        try:
            for data in job.frames:
                frame = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
                if frame is None:
                    continue
                if frame.shape[:2] != (height, width):
                    frame = cv2.resize(frame, (width, height))
                writer.write(frame)
        finally:
            writer.release()

        job.file_path = file_path.resolve().as_posix()
        job.file_hash = compute_sha256(job.file_path)
        job.fps = fps
        job.duration_seconds = int(round(len(job.frames) / fps))
        job.frames = []  # release memory
        Path(str(file_path) + ".sha256").write_text(f"{job.file_hash}  {file_path.name}\n", encoding="utf-8")
        return job.file_path, job.file_hash

    async def save_clip(self, alert_id: str, camera_id: str, stream, duration_seconds: int = 15,
                        track_id: int = -1, fps: Optional[float] = None) -> Tuple[str, str]:
        """Standalone clip capture from a stream (used outside the main loop, e.g. manual captures)."""
        fps = fps or getattr(stream, "fps", 30.0) or 30.0
        job = self.start_clip(alert_id, camera_id, track_id, fps, duration_after=duration_seconds)
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            frame = await asyncio.to_thread(stream.read)
            if frame is not None:
                self.add_frame(frame)
            else:
                await asyncio.sleep(1.0 / fps)
        with self._lock:
            self._active_clips.pop(alert_id, None)
        return await asyncio.to_thread(self.write_clip, job)

    async def finalize_and_upload(self, job: ClipJob, client) -> Dict:
        """Write the clip, then upload it and verify the backend computed the same SHA-256."""
        file_path, local_hash = await asyncio.to_thread(self.write_clip, job)
        record = {"alert_id": job.alert_id, "file_path": file_path, "sha256": local_hash, "uploaded": False}
        try:
            response = await client.upload_evidence(file_path, job.camera_id, alert_id=job.alert_id, track_id=job.track_id)
            if response.get("file_hash") != local_hash:
                logger.error("[%s] HASH MISMATCH after upload of %s: local=%s backend=%s",
                             job.camera_id, job.alert_id, local_hash, response.get("file_hash"))
                record["error"] = "hash_mismatch"
                return record
            record.update({"uploaded": True, "evidence_id": response.get("evidence_id")})
            # The backend now holds the verified copy; the local file and sidecar are no longer needed.
            await asyncio.to_thread(Path(file_path).unlink)
            await asyncio.to_thread(Path(file_path + ".sha256").unlink, True)
            logger.info("[%s] clip %s uploaded (evidence_id=%s, sha256 verified)",
                        job.camera_id, job.alert_id, response.get("evidence_id"))
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            logger.warning("[%s] clip %s kept locally for later upload: %s", job.camera_id, job.alert_id, exc)
        finally:
            job.finished = True
            self.completed.append(job)
        return record

    # ------------------------------------------------------------------ local audit trail

    def append_manifest(self, entry: Dict) -> None:
        """Append-only JSONL record of every evidence file the ML process produced."""
        manifest = Path(ml_config.evidence_path) / "ml_evidence_manifest.jsonl"
        entry = {"recorded_at": datetime.now(timezone.utc).isoformat(), **entry}
        with self._lock, open(manifest, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
