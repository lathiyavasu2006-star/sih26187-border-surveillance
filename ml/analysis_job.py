"""Worker process for backend video-analysis jobs.

    python -m ml.analysis_job --video PATH --job-file JOB.json [--camera CAM-N-001 | --standalone]

Runs VideoUploadProcessor over the file and reports through JOB.json, which is replaced atomically so the
backend never reads a half-written file:
    {"status": "loading" | "running" | "complete" | "failed", "progress": {...}, "result": {...}, "error": str}
Camera jobs use that camera's zones and save annotated snapshots into the evidence store (the backend turns
high-risk findings into alerts). Standalone jobs use no zones and store nothing.
"""
import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger("sih26187.ml.analysis_job")

PROGRESS_INTERVAL_SECONDS = 0.25
FRAME_MAX_WIDTH = 960


class JobReporter:
    def __init__(self, job_file: Path, frames_total: int) -> None:
        self.job_file = job_file
        self.frames_total = max(0, frames_total)
        self.progress: Dict[str, Any] = {"frames_total": self.frames_total, "percent": 0.0}
        self._last_write = 0.0

    def write(self, status: str, result: Optional[Dict] = None, error: Optional[str] = None) -> None:
        payload = {"status": status, "progress": self.progress, "result": result, "error": error,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        partial = self.job_file.with_suffix(".tmp")
        partial.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(partial, self.job_file)

    @property
    def frame_file(self) -> Path:
        return self.job_file.with_suffix(".jpg")

    def write_frame(self, annotated) -> None:
        """Latest annotated frame for the live view (JPEG, replaced atomically)."""
        import cv2

        height, width = annotated.shape[:2]
        if width > FRAME_MAX_WIDTH:
            annotated = cv2.resize(annotated, (FRAME_MAX_WIDTH, int(height * FRAME_MAX_WIDTH / width)))
        ok, buffer = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if not ok:
            return
        partial = self.frame_file.with_suffix(".jpg.tmp")
        partial.write_bytes(buffer.tobytes())
        os.replace(partial, self.frame_file)

    def update(self, values: Dict[str, Any]) -> None:
        self.progress.update(values)
        if self.frames_total:
            self.progress["percent"] = round(min(99.9, 100.0 * values.get("frames_read", 0) / self.frames_total), 1)
        now = time.monotonic()
        if now - self._last_write >= PROGRESS_INTERVAL_SECONDS:
            self._last_write = now
            self.write("running")


def count_frames(video_path: str) -> int:
    import cv2

    capture = cv2.VideoCapture(video_path)
    try:
        return int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()


async def run(args: argparse.Namespace) -> int:
    from ml.config import ml_config
    from ml.detector import detector
    from ml.video_upload import video_processor

    reporter = JobReporter(Path(args.job_file), count_frames(args.video))
    reporter.write("loading")
    if not detector.loaded and not await asyncio.to_thread(detector.load):
        reporter.write("failed", error="Detector failed to load")
        return 2

    zones = [] if args.standalone else None  # None: the processor loads the camera's zones from the backend
    recorded_at = datetime.fromisoformat(args.recorded_at) if args.recorded_at else None
    reporter.write("running")
    result = await video_processor.process(
        args.video,
        args.camera or "STANDALONE",
        frame_step=args.frame_step,
        zones=zones,
        save_snapshots=not args.standalone,
        recorded_at=recorded_at,
        on_progress=reporter.update,
        on_frame=reporter.write_frame,
    )
    if result.get("error"):
        reporter.write("failed", error=result["error"])
        return 3
    reporter.progress.update({
        "percent": 100.0,
        "frames_read": result.get("frames_read", 0),
        "persons": len(result.get("persons_found") or []),
        "vehicles": len(result.get("vehicles_found") or []),
        "animals": len(result.get("animals_found") or []),
        "weapons": sum(len(carriers) for carriers in (result.get("weapons_found") or {}).values()),
        "alerts": len(result.get("alerts_fired") or []),
        "video_seconds": result.get("duration_seconds", 0),
    })
    reporter.write("complete", result=result)
    return 0


def main() -> None:
    from ml.config import ml_config

    parser = argparse.ArgumentParser(description="SIH26187 video analysis worker")
    parser.add_argument("--video", required=True)
    parser.add_argument("--job-file", required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--camera")
    target.add_argument("--standalone", action="store_true")
    parser.add_argument("--frame-step", type=int, default=ml_config.analysis_frame_step)
    parser.add_argument("--image-size", type=int, default=ml_config.analysis_image_size)
    parser.add_argument("--recorded-at")
    args = parser.parse_args()

    # Offline analysis trades speed for recall: a larger input finds the people a live 640 px pass misses.
    # Only this worker process is affected, and the TensorRT engine (built for the live size) steps aside.
    if args.image_size and args.image_size != ml_config.image_size:
        ml_config.image_size = int(args.image_size)
        ml_config.use_tensorrt = ml_config.analysis_use_tensorrt

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    try:
        code = asyncio.run(run(args))
    except Exception as exc:  # report every failure to the backend through the job file
        logger.exception("analysis job failed")
        try:
            JobReporter(Path(args.job_file), 0).write("failed", error=str(exc))
        except OSError:
            pass
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
