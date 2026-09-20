"""Performance profiler for the ML pipeline.

    C:\\pythonjarvis\\python.exe -m ml.benchmark --source 0 --seconds 8 --frames 150

Measures, separately, so a bottleneck is never guessed:
  1. camera delivery rate (what the source itself provides),
  2. YOLO latency split into preprocess / inference / postprocess,
  3. every pipeline stage on recorded frames processed as fast as possible (no camera wait, no network).
"""
import argparse
import asyncio
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from ml.config import ml_config  # noqa: E402


def summarize(values: List[float]) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)
    p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
    return f"median {statistics.median(ordered):6.1f} ms | p90 {p90:6.1f} ms | mean {statistics.fmean(ordered):6.1f} ms"


def measure_camera(source, seconds: float, keep: int) -> List[np.ndarray]:
    from ml.camera_stream import CameraStream
    from ml.night import night_enhancer

    stream = CameraStream(source, "BENCH")
    if not stream.start():
        raise SystemExit(f"cannot open source {source}")
    time.sleep(1.0)  # let auto-exposure settle
    start_count, started = stream.frames_read, time.monotonic()
    frames: List[np.ndarray] = []
    while time.monotonic() - started < seconds:
        frame = stream.read(timeout=1.0)
        if frame is not None and len(frames) < keep:
            frames.append(frame.copy())
    delivered = (stream.frames_read - start_count) / (time.monotonic() - started)
    print(f"[1] camera: {stream.resolution[0]}x{stream.resolution[1]}, driver reports {stream.fps:.1f} fps, "
          f"actually delivered {delivered:.1f} fps, brightness {night_enhancer.get_brightness(frames[-1]):.0f}")
    stream.release()
    return frames


def measure_detector(frames: List[np.ndarray]) -> None:
    from ml.detector import detector

    if not detector.loaded and not detector.load():
        raise SystemExit("detector failed to load")
    pre, infer, post, wrapper = [], [], [], []
    for frame in frames:
        t0 = time.perf_counter()
        with detector._lock:
            results = detector.model.predict(frame, conf=ml_config.tracker_input_conf, iou=ml_config.iou_threshold,
                                             classes=detector.target_classes, imgsz=ml_config.image_size,
                                             device=detector.device, half=detector.half, verbose=False)
        total = (time.perf_counter() - t0) * 1000
        speed = results[0].speed
        pre.append(speed["preprocess"])
        infer.append(speed["inference"])
        post.append(speed["postprocess"])
        wrapper.append(total)
    print(f"[2] YOLO {Path(ml_config.model_path).name} imgsz={ml_config.image_size} fp16={detector.half}")
    print(f"      preprocess   {summarize(pre)}")
    print(f"      inference    {summarize(infer)}")
    print(f"      postprocess  {summarize(post)}")
    print(f"      predict()    {summarize(wrapper)}  -> ceiling {1000 / statistics.median(wrapper):.1f} fps")


class NullWS:
    connected = True
    dropped_frames = 0

    async def connect(self, max_attempts=None):
        return True

    async def send_frame(self, payload, critical=False):
        return True

    async def flush_pending(self, max_attempts=3):
        return 0

    async def close(self):
        return None


class StaticZones:
    def __init__(self, zones):
        self.zones = zones

    async def load_zones(self, camera_id, force=False):
        return self.zones


def measure_pipeline(frames: List[np.ndarray]) -> None:
    import ml.pipeline as pipeline_module
    from ml.detector import detector
    from ml.pipeline import Pipeline

    height, width = frames[0].shape[:2]
    zones = [{"zone_id": 1, "zone_name": "Bench", "zone_type": "restricted", "risk_bonus": 50, "is_active": True,
              "polygon": [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
              "loiter_threshold_seconds": 30, "night_rules": {"multiplier": 1.5, "start": 22, "end": 5}}]

    class ReplayStream:
        is_file, ended, fps = False, False, 30.0

    ticks = iter(range(10**9))
    base = datetime.now(timezone.utc)
    pipe = Pipeline("BENCH", "replay", stream=ReplayStream(), ws_client=NullWS(), detector=detector,
                    fence=StaticZones(zones), clock=lambda: base + timedelta(seconds=next(ticks) / 30))

    stages: Dict[str, List[float]] = {}
    originals = {}

    def timed(name, fn):
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                stages.setdefault(name, []).append((time.perf_counter() - t0) * 1000)
        return wrapper

    def timed_async(name, fn):
        async def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return await fn(*args, **kwargs)
            finally:
                stages.setdefault(name, []).append((time.perf_counter() - t0) * 1000)
        return wrapper

    pipe._enhance_and_buffer = timed("enhance+buffer", pipe._enhance_and_buffer)
    pipe.tracker.track = timed("track", pipe.tracker.track)
    pipe.analyzer.analyze = timed("analyze", pipe.analyzer.analyze)
    pipe._publish_frame = timed_async("publish (annotate+jpeg+send)", pipe._publish_frame)
    pipe._handle_alert = timed_async("alert (snapshot)", pipe._handle_alert)
    detect_original = detector.detect
    detector.detect = timed("detect", detect_original)
    original_encode = pipe._encode_frame
    pipe._encode_frame = timed("  of which jpeg encode", original_encode)

    async def run():
        totals = []
        for frame in frames:
            t0 = time.perf_counter()
            await pipe.process_frame(frame)
            totals.append((time.perf_counter() - t0) * 1000)
        return totals

    import tempfile

    saved = (ml_config.record_clips, ml_config.snapshots_path, ml_config.evidence_path)
    with tempfile.TemporaryDirectory(prefix="sih26187-bench-") as scratch:
        # Benchmark alerts must never write into the real evidence store.
        ml_config.record_clips, ml_config.snapshots_path, ml_config.evidence_path = False, scratch, scratch
        try:
            totals = asyncio.run(run())
        finally:
            ml_config.record_clips, ml_config.snapshots_path, ml_config.evidence_path = saved
            detector.detect = detect_original
    print(f"[3] pipeline stages over {len(frames)} frames (ws_send_fps={ml_config.ws_send_fps}):")
    for name, values in stages.items():
        print(f"      {name:30} {summarize(values)}  (calls={len(values)})")
    median_total = statistics.median(totals)
    print(f"      {'TOTAL process_frame':30} {summarize(totals)}  -> processing ceiling {1000 / median_total:.1f} fps")


def main() -> None:
    parser = argparse.ArgumentParser(description="ML pipeline profiler")
    parser.add_argument("--source", default="0")
    parser.add_argument("--seconds", type=float, default=8.0)
    parser.add_argument("--frames", type=int, default=150)
    args = parser.parse_args()
    source = int(args.source) if args.source.isdigit() else args.source
    frames = measure_camera(source, args.seconds, args.frames)
    measure_detector(frames[: min(len(frames), 80)])
    measure_pipeline(frames)


if __name__ == "__main__":
    main()
