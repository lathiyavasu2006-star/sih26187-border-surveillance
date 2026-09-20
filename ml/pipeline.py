"""Per-camera pipeline: capture → enhance → detect → track → analyse → evidence → publish.

Backend contract (Week 2, unchanged):
* frames:  `frame_data` over /ws/{camera_id} at up to ML_WS_SEND_FPS with the annotated JPEG; detections are
  attached at ML_DETECTION_PUBLISH_HZ so tracked_objects does not grow by dozens of rows per second;
* alerts:  sent immediately in their own `frame_data` message carrying `snapshot_path` and no frame, so the
  backend registers the ML-written, already-hashed snapshot instead of writing a second copy
  (Week 4 note: a `frame_update` with `frame: null` carries alerts only);
* clips:   recorded without blocking, then uploaded to /evidence/upload with an end-to-end hash check.
"""
import asyncio
import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Set

import cv2
import numpy as np

from ml.analyzer import FrameAnalysis, FrameAnalyzer, split_weapons, to_ws_alert, to_ws_detection
from ml.weapons import weapon_detector
from ml.annotator import annotator
from ml.backend_client import BackendClient, backend_client
from ml.camera_stream import CameraStream
from ml.config import ml_config
from ml.detector import YOLOv8Detector, detector as shared_detector
from ml.event_memory import EventMemory
from ml.evidence import EvidenceCapture
from ml.fence import FenceChecker, fence_checker as shared_fence_checker
from ml.night import night_enhancer
from ml.identity import IdentityResolver
from ml.tracker import ByteTracker
from ml.ws_client import WSClient

logger = logging.getLogger("sih26187.ml.pipeline")

STATUS_LOG_SECONDS = float(__import__('os').environ.get('ML_STATUS_LOG_SECONDS', '30'))
CLEANUP_SECONDS = 10
# Overlapping clips share frame bytes, but an alert storm must not start unbounded recordings.
MAX_ACTIVE_CLIPS_PER_CAMERA = 3


class _TrailView:
    """Read-only snapshot of trails for rendering in a worker thread while the tracker keeps updating."""

    def __init__(self, trails: Dict[int, list]):
        self._trails = trails

    def get_trail(self, track_id: int) -> list:
        return self._trails.get(track_id, [])


class Pipeline:
    def __init__(
        self,
        camera_id: str,
        source,
        token: Optional[str] = None,
        client: Optional[BackendClient] = None,
        stream: Optional[CameraStream] = None,
        ws_client: Optional[WSClient] = None,
        detector: Optional[YOLOv8Detector] = None,
        fence: Optional[FenceChecker] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ):
        self.camera_id = camera_id
        self.source = source
        self.token = token  # kept for API compatibility; authentication is shared via BackendClient
        self.client = client or backend_client
        self.stream = stream or CameraStream(source, camera_id)
        self.detector = detector or shared_detector
        self.fence = fence or shared_fence_checker
        self.memory = EventMemory()
        self.tracker = ByteTracker(frame_rate=self.stream.fps or ml_config.target_fps, camera_id=camera_id)
        self.identity = IdentityResolver(camera_id)
        self.analyzer = FrameAnalyzer(camera_id, self.memory)
        self.evidence = EvidenceCapture()
        self.ws_client = ws_client or WSClient(camera_id, client=self.client)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

        self._running = False
        self._clip_tasks: Set[asyncio.Task] = set()
        # One worker keeps buffered evidence frames in capture order while the loop moves on to detection.
        self._buffer_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"evidence-{camera_id}")
        self._publish_task: Optional[asyncio.Task] = None
        self._alert_tasks: Set[asyncio.Task] = set()
        self.frames_skipped_publish = 0
        self.zones: List[Dict] = []
        self.mode = "normal"
        self.current_fps = 0.0
        self.fps_counter = 0
        self.fps_start = time.monotonic()
        self._last_send = 0.0
        self._last_detection_publish = 0.0
        self._last_status_log = time.monotonic()
        self._status_frames_read = 0
        self._status_frames_processed = 0
        self.last_brightness = 0.0
        self._last_cleanup = time.monotonic()

        # Counters for status and tests
        self.frames_processed = 0
        self.alerts_sent = 0
        self.frames_sent = 0
        self.last_analysis: Optional[FrameAnalysis] = None
        self.alert_records: List[Dict] = []
        self.clip_records: List[Dict] = []

    # ------------------------------------------------------------------ lifecycle

    async def run(self, duration: Optional[float] = None, max_frames: Optional[int] = None) -> None:
        started = await asyncio.to_thread(self.stream.start)
        if not started and self.stream.is_file:
            logger.error("[%s] cannot open video source %s", self.camera_id, self.source)
            return
        if not started:
            logger.warning("[%s] camera not reachable yet; the stream keeps reconnecting in the background", self.camera_id)

        if not await self.ws_client.connect(max_attempts=1):
            logger.warning("[%s] backend WebSocket unavailable; processing continues and reconnects in background",
                           self.camera_id)
        self.zones = await self.fence.load_zones(self.camera_id)
        self._running = True
        deadline = time.monotonic() + duration if duration else None
        logger.info("[%s] pipeline started — %d zone(s), source=%s", self.camera_id, len(self.zones), self.source)

        try:
            while self._running:
                if deadline and time.monotonic() >= deadline:
                    break
                if max_frames is not None and self.frames_processed >= max_frames:
                    break
                try:
                    frame = await asyncio.to_thread(self.stream.read, 1.0)
                    if frame is None:
                        if self.stream.is_file and self.stream.ended:
                            logger.info("[%s] end of video source", self.camera_id)
                            break
                        await asyncio.sleep(0.05)
                        continue
                    await self.process_frame(frame)
                    await asyncio.sleep(0)  # let the WebSocket reader and other cameras run
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("[%s] pipeline error", self.camera_id)
                    await asyncio.sleep(1)
        finally:
            await self.shutdown()

    def stop(self) -> None:
        self._running = False

    async def shutdown(self) -> None:
        self._running = False
        if self._alert_tasks:  # every raised alert is written, hashed and sent before shutdown completes
            await asyncio.gather(*self._alert_tasks, return_exceptions=True)
        if self._publish_task is not None:
            await asyncio.gather(self._publish_task, return_exceptions=True)
        await asyncio.to_thread(self._buffer_executor.shutdown, True)  # every buffered frame lands before clips close
        # Finish every clip that was recording so no evidence is lost on shutdown.
        for job in self.evidence.due_clips(now=float("inf")):
            self._schedule_clip(job)
        if self._clip_tasks:
            await asyncio.gather(*self._clip_tasks, return_exceptions=True)
        await asyncio.to_thread(self.stream.release)
        try:
            unsent = await asyncio.wait_for(self.ws_client.flush_pending(), timeout=30)
        except asyncio.TimeoutError:
            unsent = self.ws_client.undelivered_alerts()
        if unsent:
            logger.error("[%s] %d alert(s) could not be delivered before shutdown; they remain in the local "
                         "evidence manifest with their snapshots", self.camera_id, unsent)
        await self.ws_client.close()
        logger.info("[%s] pipeline stopped: %d frames, %d alerts, %d messages sent",
                    self.camera_id, self.frames_processed, self.alerts_sent, self.frames_sent)

    # ------------------------------------------------------------------ per frame

    def _enhance_and_buffer(self, frame: np.ndarray, monotonic_now: float):
        # The raw frame is JPEG-encoded into the pre-alert buffer on the dedicated worker, not inline.
        self._buffer_executor.submit(self.evidence.add_frame, frame, monotonic_now)
        self.last_brightness, _ = night_enhancer.frame_statistics(frame)
        return night_enhancer.check_and_enhance(frame)

    async def process_frame(self, frame: np.ndarray) -> FrameAnalysis:
        now = self.clock()
        mono = time.monotonic()

        self.zones = await self.fence.load_zones(self.camera_id)  # cached for 30 s inside FenceChecker
        enhanced, _, self.mode = await asyncio.to_thread(self._enhance_and_buffer, frame, mono)

        raw = await asyncio.to_thread(self.detector.detect, enhanced, ml_config.tracker_input_conf)
        trackable, weapons = split_weapons(raw)
        tracked = self.tracker.track(enhanced, trackable)
        if ml_config.reid_enabled:
            tracked = self.identity.resolve(enhanced, tracked)  # stable ids for returning people
        self.memory.mark_missing_as_lost({d["track_id"] for d in tracked}, now=now)
        weapons += await self._weapons_near_people(enhanced, tracked)

        analysis = self.analyzer.analyze(tracked, weapons, self.zones, now=now)
        self.last_analysis = analysis
        self.frames_processed += 1
        self._update_fps(mono)

        if analysis.alerts:
            detections = [dict(d) for d in analysis.detections]
            trails = {d["track_id"]: self.memory.get_trail(d["track_id"]) for d in detections}
            for candidate in analysis.alerts:
                self._schedule_alert(enhanced, analysis, detections, trails, candidate)

        for job in self.evidence.due_clips(now=mono):
            self._schedule_clip(job)

        await self._publish_frame(enhanced, analysis, mono, now)

        if mono - self._last_cleanup >= CLEANUP_SECONDS:
            self.memory.cleanup_old_tracks(now=now)
            self._last_cleanup = mono
        if mono - self._last_status_log >= STATUS_LOG_SECONDS:
            self._log_status(analysis)
            self._last_status_log = mono
        return analysis

    async def _weapons_near_people(self, frame, tracked) -> list:
        """Weapon-model pass over the people in this frame (throttled: a weapon does not appear for one frame)."""
        if not ml_config.weapon_model_enabled:
            return []
        every = max(1, ml_config.weapon_every_n_frames)
        if self.frames_processed % every:
            return []
        people = [det for det in tracked if ml_config.is_person(det.get("cls_name", ""))]
        if not people:
            return []
        return await asyncio.to_thread(weapon_detector.detect, frame, people)

    def _update_fps(self, mono: float) -> None:
        self.fps_counter += 1
        elapsed = mono - self.fps_start
        if elapsed >= 1.0:
            self.current_fps = round(self.fps_counter / elapsed, 1)
            self.fps_counter = 0
            self.fps_start = mono

    def _stats(self, analysis: FrameAnalysis) -> Dict:
        return {
            "people_count": analysis.people_count,
            "vehicle_count": analysis.vehicle_count,
            "animal_count": analysis.animal_count,
            "active_alerts": len(analysis.alerts),
            "fps": float(self.current_fps),
        }

    def _schedule_alert(self, frame: np.ndarray, analysis: FrameAnalysis, detections, trails, candidate) -> None:
        """Start the evidence clip now (so the pre-alert buffer lines up with the event) and write the snapshot,
        manifest entry and backend message in the background so an alert burst never stalls detection."""
        if ml_config.record_clips and self.evidence.active_clip_count() < MAX_ACTIVE_CLIPS_PER_CAMERA:
            self.evidence.start_clip(candidate.alert_id, self.camera_id, candidate.track_id,
                                     fps=self.current_fps or self.stream.fps or ml_config.target_fps)
        task = asyncio.get_running_loop().create_task(
            self._handle_alert(frame, analysis, candidate, detections=detections, trails=trails,
                               zones=list(self.zones), fps=self.current_fps, mode=self.mode, start_clip=False),
            name=f"alert-{candidate.alert_id}",
        )
        self._alert_tasks.add(task)
        task.add_done_callback(self._alert_tasks.discard)

    async def _handle_alert(self, frame: np.ndarray, analysis: FrameAnalysis, candidate, detections=None, trails=None,
                            zones=None, fps=None, mode=None, start_clip: bool = True) -> None:
        detections = analysis.detections if detections is None else detections
        memory_view = self.memory if trails is None else _TrailView(trails)
        zones = self.zones if zones is None else zones
        fps = self.current_fps if fps is None else fps
        mode = self.mode if mode is None else mode

        def render():
            return annotator.draw_frame(frame, detections, memory_view, zones, camera_id=self.camera_id,
                                        fps=fps, mode=mode, highlight_track_id=candidate.track_id)

        snapshot_path, snapshot_hash = None, None
        try:
            annotated = await asyncio.to_thread(render)
            snapshot_path, snapshot_hash = await self.evidence.capture_snapshot(candidate.alert_id, self.camera_id, annotated)
        except Exception as exc:
            logger.error("[%s] snapshot for %s failed: %s", self.camera_id, candidate.alert_id, exc)

        record = {
            "alert_id": candidate.alert_id,
            "alert_type": candidate.alert_type,
            "track_id": candidate.track_id,
            "risk_score": candidate.risk["risk_score"],
            "risk_level": candidate.risk["risk_level"],
            "risk_reasons": candidate.risk["risk_reasons"],
            "snapshot_path": snapshot_path,
            "snapshot_sha256": snapshot_hash,
        }
        self.alert_records.append(record)
        await asyncio.to_thread(self.evidence.append_manifest, {"camera_id": self.camera_id, "type": "snapshot", **record})

        if start_clip and ml_config.record_clips and self.evidence.active_clip_count() < MAX_ACTIVE_CLIPS_PER_CAMERA:
            self.evidence.start_clip(candidate.alert_id, self.camera_id, candidate.track_id,
                                     fps=self.current_fps or self.stream.fps or ml_config.target_fps)

        payload = {
            "type": "frame_data",
            "camera_id": self.camera_id,
            "frame": None,
            "detections": [],
            "alerts": [to_ws_alert(candidate, snapshot_path)],
            "stats": self._stats(analysis),
            "timestamp": candidate.created_at.isoformat(),
        }
        if await self.ws_client.send_frame(payload, critical=True):
            self.alerts_sent += 1
        logger.warning("[%s] ALERT %s %s track=%d risk=%d (%s) %s", self.camera_id, candidate.alert_id,
                       candidate.alert_type, candidate.track_id, candidate.risk["risk_score"],
                       candidate.risk["risk_level"], ", ".join(candidate.risk["risk_reasons"]))

    def _schedule_clip(self, job) -> None:
        async def _run():
            record = await self.evidence.finalize_and_upload(job, self.client)
            self.clip_records.append(record)
            await asyncio.to_thread(self.evidence.append_manifest, {"camera_id": self.camera_id, "type": "clip", **record})

        task = asyncio.get_running_loop().create_task(_run(), name=f"clip-{job.alert_id}")
        self._clip_tasks.add(task)
        task.add_done_callback(self._clip_tasks.discard)

    def _encode_frame(self, annotated: np.ndarray) -> str:
        height, width = annotated.shape[:2]
        if width > ml_config.ws_max_frame_width:
            scale = ml_config.ws_max_frame_width / width
            annotated = cv2.resize(annotated, (ml_config.ws_max_frame_width, int(height * scale)))
        ok, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, ml_config.ws_jpeg_quality])
        if not ok:
            raise ValueError("JPEG encoding failed")
        return base64.b64encode(buffer.tobytes()).decode("ascii")

    async def _publish_frame(self, frame: np.ndarray, analysis: FrameAnalysis, mono: float, now: datetime) -> None:
        """Schedule publishing without holding up detection of the next frame (latest frame wins)."""
        if ml_config.ws_send_fps <= 0 or mono - self._last_send < 1.0 / ml_config.ws_send_fps:
            return
        if self._publish_task is not None and not self._publish_task.done():
            self.frames_skipped_publish += 1
            return
        self._last_send = mono
        detections = [dict(d) for d in analysis.detections]
        trails = {d["track_id"]: self.memory.get_trail(d["track_id"]) for d in detections}
        self._publish_task = asyncio.get_running_loop().create_task(
            self._publish(frame, analysis, detections, trails, list(self.zones), self.current_fps, self.mode, mono, now)
        )

    async def _publish(self, frame, analysis, detections, trails, zones, fps, mode, mono, now) -> None:
        try:
            await self._publish_now(frame, analysis, detections, trails, zones, fps, mode, mono, now)
        except Exception:
            logger.exception("[%s] frame publish failed", self.camera_id)

    async def _publish_now(self, frame, analysis, detections, trails, zones, fps, mode, mono, now) -> None:
        memory_view = _TrailView(trails)

        def render() -> str:
            annotated = annotator.draw_frame(frame, detections, memory_view, zones,
                                             camera_id=self.camera_id, fps=fps, mode=mode)
            return self._encode_frame(annotated)

        frame_b64 = await asyncio.to_thread(render)

        published: List[Dict] = []
        if ml_config.detection_publish_hz > 0 and mono - self._last_detection_publish >= 1.0 / ml_config.detection_publish_hz:
            published = [to_ws_detection(d) for d in detections]
            self._last_detection_publish = mono

        payload = {
            "type": "frame_data",
            "camera_id": self.camera_id,
            "frame": frame_b64,
            "detections": published,
            "alerts": [],
            "stats": self._stats(analysis),
            "timestamp": now.isoformat(),
        }
        if await self.ws_client.send_frame(payload):
            self.frames_sent += 1

    def _log_status(self, analysis: FrameAnalysis) -> None:
        # camera fps = frames the source delivered; fps = frames analysed. The gap shows which one limits.
        elapsed = max(1e-6, time.monotonic() - self._last_status_log)
        frames_read = getattr(self.stream, "frames_read", 0)
        camera_fps = (frames_read - self._status_frames_read) / elapsed
        processed_fps = (self.frames_processed - self._status_frames_processed) / elapsed
        self._status_frames_read, self._status_frames_processed = frames_read, self.frames_processed
        logger.info(
            "[%s] fps=%.1f camera_fps=%.1f brightness=%.0f infer=%.0fms mode=%s people=%d vehicles=%d animals=%d "
            "tracks=%d identities=%d reid=%d ws=%s dropped=%d alerts=%d clips=%d",
            self.camera_id, processed_fps, camera_fps, self.last_brightness, self.detector.inference_ms, self.mode,
            analysis.people_count,
            analysis.vehicle_count, analysis.animal_count, len(self.memory.get_active_tracks()),
            self.identity.identity_count, self.identity.reidentified,
            "up" if self.ws_client.connected else "down", self.ws_client.dropped_frames, self.alerts_sent,
            self.evidence.active_clip_count(),
        )
