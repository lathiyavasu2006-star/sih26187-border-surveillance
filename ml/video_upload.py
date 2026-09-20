"""Offline analysis of an uploaded video with the same logic as the live pipeline.

Time-based rules (loitering, speed, cooldowns) use the video's own timeline, so a 10-minute clip analysed
in 40 seconds still measures dwell times correctly.
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

from ml.analyzer import FrameAnalyzer, split_weapons
from ml.annotator import annotator
from ml.camera_stream import CameraStream
from ml.config import ml_config
from ml.detector import YOLOv8Detector, detector as shared_detector
from ml.event_memory import EventMemory
from ml.evidence import EvidenceCapture
from ml.fence import FenceChecker, fence_checker as shared_fence_checker
from ml.night import night_enhancer
from ml.identity import IdentityResolver
from ml.tracker import ByteTracker

logger = logging.getLogger("sih26187.ml.video_upload")


class VideoUploadProcessor:
    def __init__(self, detector: Optional[YOLOv8Detector] = None, fence: Optional[FenceChecker] = None):
        self.detector = detector or shared_detector
        self.fence = fence or shared_fence_checker

    async def process(
        self,
        video_path: str,
        camera_id: str,
        frame_step: int = 0,  # 0 = ml_config.analysis_frame_step
        zones: Optional[List[Dict]] = None,
        save_snapshots: bool = True,
        recorded_at: Optional[datetime] = None,
        on_progress: Optional[Callable[[Dict], None]] = None,
        on_frame: Optional[Callable[[np.ndarray], None]] = None,
        frame_interval_seconds: float = 0.25,
    ) -> Dict:
        if not Path(video_path).is_file():
            return {"error": f"Video file not found: {video_path}"}
        stream = CameraStream(video_path, camera_id, realtime=False)
        if not await asyncio.to_thread(stream.start):
            return {"error": "Cannot open video file"}

        frame_step = int(frame_step) or ml_config.analysis_frame_step
        if frame_step <= 0:
            # Every frame is what finds people who cross quickly; only a long recording is sampled, and then
            # as densely as the frame budget allows.
            total_frames = stream.frame_count or 0
            frame_step = max(1, -(-total_frames // max(1, ml_config.analysis_max_frames))) if total_frames else 1
            logger.info("[%s] analysing every %s frame(s) of %s", camera_id, frame_step, total_frames or "unknown")
        frame_step = max(1, frame_step)
        base_time = (recorded_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        memory = EventMemory()
        analyzer = FrameAnalyzer(camera_id, memory)
        tracker = ByteTracker(frame_rate=(stream.fps or ml_config.target_fps) / frame_step, camera_id=camera_id)
        # Identities use the video's own clock so the re-identification window follows the footage.
        identity = IdentityResolver(camera_id, clock=stream.position_seconds)
        evidence = EvidenceCapture()
        if zones is None:
            zones = await self.fence.load_zones(camera_id)

        result: Dict = {
            "camera_id": camera_id,
            "video_path": str(Path(video_path).resolve().as_posix()),
            "fps": stream.fps,
            "resolution": list(stream.resolution),
            "zones_used": len(zones),
            "persons_found": [],
            "vehicles_found": [],
            "animals_found": [],
            "alerts_fired": [],
            "timeline": [],
            "frames_read": 0,
            "frames_processed": 0,
            "duration_seconds": 0.0,
            "evidence_saved": [],
        }

        frame_count = 0
        last_frame_emit = float("-inf")
        try:
            while True:
                frame = await asyncio.to_thread(stream.read)
                if frame is None:
                    break
                frame_count += 1
                if frame_count % frame_step != 0:
                    continue

                video_seconds = stream.position_seconds()
                now = base_time + timedelta(seconds=video_seconds)
                enhanced, _, mode = await asyncio.to_thread(night_enhancer.check_and_enhance, frame)
                raw = await asyncio.to_thread(self.detector.detect, enhanced, ml_config.tracker_input_conf)
                trackable, weapons = split_weapons(raw)
                tracked = tracker.track(enhanced, trackable)
                if ml_config.reid_enabled:
                    tracked = identity.resolve(enhanced, tracked)
                memory.mark_missing_as_lost({d["track_id"] for d in tracked}, now=now)
                analysis = analyzer.analyze(tracked, weapons, zones, now=now)
                result["frames_processed"] += 1

                for det in analysis.detections:
                    track_id, cls_name = det["track_id"], det["cls_name"]
                    bucket = ("persons_found" if ml_config.is_person(cls_name)
                              else "vehicles_found" if ml_config.is_vehicle(cls_name)
                              else "animals_found" if ml_config.is_animal(cls_name) else None)
                    if bucket and track_id not in result[bucket]:
                        result[bucket].append(track_id)

                if analysis.detections:
                    result["timeline"].append({
                        "video_seconds": round(video_seconds, 2),
                        "people": analysis.people_count,
                        "vehicles": analysis.vehicle_count,
                        "animals": analysis.animal_count,
                        "max_risk_score": max(d["risk_score"] for d in analysis.detections),
                        "mode": mode,
                    })

                for candidate in analysis.alerts:
                    alert = {
                        "alert_id": candidate.alert_id,
                        "alert_type": candidate.alert_type,
                        "track_id": candidate.track_id,
                        "video_seconds": round(video_seconds, 2),
                        "risk_score": candidate.risk["risk_score"],
                        "risk_level": candidate.risk["risk_level"],
                        "risk_reasons": candidate.risk["risk_reasons"],
                    }
                    if save_snapshots:
                        annotated = annotator.draw_frame(enhanced, analysis.detections, memory, zones,
                                                         camera_id=camera_id, mode=mode,
                                                         highlight_track_id=candidate.track_id)
                        path, digest = await evidence.capture_snapshot(candidate.alert_id, camera_id, annotated)
                        alert["snapshot_path"] = path
                        result["evidence_saved"].append({"alert_id": candidate.alert_id, "file_path": path,
                                                         "sha256": digest, "video_seconds": alert["video_seconds"]})
                    result["alerts_fired"].append(alert)

                if on_frame is not None and time.monotonic() - last_frame_emit >= frame_interval_seconds:
                    # Live preview for the operator: the analysed frame with the same HUD as the live feed.
                    last_frame_emit = time.monotonic()
                    on_frame(annotator.draw_frame(enhanced, analysis.detections, memory, zones,
                                                  camera_id=camera_id, mode=mode))

                if on_progress is not None:
                    on_progress({
                        "frames_read": frame_count,
                        "video_seconds": round(video_seconds, 2),
                        "persons": len(result["persons_found"]),
                        "vehicles": len(result["vehicles_found"]),
                        "animals": len(result["animals_found"]),
                        "alerts": len(result["alerts_fired"]),
                    })
        finally:
            result["frames_read"] = frame_count
            result["duration_seconds"] = round(stream.position_seconds(), 2)
            await asyncio.to_thread(stream.release)

        logger.info("[%s] video analysed: %d frames (%d processed), %d people, %d alerts", camera_id,
                    result["frames_read"], result["frames_processed"], len(result["persons_found"]),
                    len(result["alerts_fired"]))
        return result


video_processor = VideoUploadProcessor()
