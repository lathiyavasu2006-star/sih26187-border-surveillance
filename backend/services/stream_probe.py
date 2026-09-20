"""OpenCV stream probing (blocking; always call through probe_stream_async)."""
import asyncio
import base64
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("sih26187.stream_probe")

PREVIEW_MAX_WIDTH = 640
FPS_SAMPLE_FRAMES = 10


@dataclass
class ProbeResult:
    connected: bool
    message: str
    fps: float = 0.0
    width: int = 0
    height: int = 0
    frame_jpeg_b64: Optional[str] = None
    latency_ms: int = 0

    @property
    def resolution(self) -> Optional[str]:
        return f"{self.width}x{self.height}" if self.width and self.height else None


def is_device_index(device_id: Optional[str]) -> bool:
    return bool(device_id) and str(device_id).strip().isdigit()


def _open_capture(source, timeout_ms: int):
    import cv2

    if isinstance(source, int):
        backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
        return cv2.VideoCapture(source, backend)
    params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms, cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms]
    return cv2.VideoCapture(source, cv2.CAP_FFMPEG, params)


def probe_stream(
    rtsp_url: Optional[str] = None,
    device_id: Optional[str] = None,
    timeout_seconds: float = 5.0,
    capture_preview: bool = True,
    measure_fps: bool = False,
) -> ProbeResult:
    """Open the stream, read a frame, optionally encode a JPEG preview and measure FPS."""
    import cv2

    if is_device_index(device_id):
        source = int(str(device_id).strip())
    elif rtsp_url:
        source = rtsp_url
    else:
        return ProbeResult(False, "No stream source configured (rtsp_url or numeric device_id required)")

    started = time.perf_counter()
    cap = None
    try:
        cap = _open_capture(source, int(timeout_seconds * 1000))
        if cap is None or not cap.isOpened():
            return ProbeResult(False, "Cannot open stream", latency_ms=int((time.perf_counter() - started) * 1000))
        ok, frame = cap.read()
        latency_ms = int((time.perf_counter() - started) * 1000)
        if not ok or frame is None:
            return ProbeResult(False, "Stream opened but frame read failed", latency_ms=latency_ms)

        height, width = frame.shape[:2]
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if measure_fps:
            sample_start, frames = time.perf_counter(), 0
            deadline = sample_start + min(timeout_seconds, 3.0)
            while frames < FPS_SAMPLE_FRAMES and time.perf_counter() < deadline:
                ok, _ = cap.read()
                if not ok:
                    break
                frames += 1
            elapsed = time.perf_counter() - sample_start
            if frames and elapsed > 0:
                fps = frames / elapsed

        preview = None
        if capture_preview:
            if width > PREVIEW_MAX_WIDTH:
                scale = PREVIEW_MAX_WIDTH / width
                frame = cv2.resize(frame, (PREVIEW_MAX_WIDTH, int(height * scale)))
            encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if encoded:
                preview = base64.b64encode(buffer.tobytes()).decode("ascii")
        return ProbeResult(True, "Stream connected", round(fps, 2), width, height, preview, latency_ms)
    except Exception as exc:
        logger.warning("Stream probe error: %s", exc)
        return ProbeResult(False, f"Probe error: {type(exc).__name__}", latency_ms=int((time.perf_counter() - started) * 1000))
    finally:
        if cap is not None:
            cap.release()


async def probe_stream_async(
    rtsp_url: Optional[str] = None,
    device_id: Optional[str] = None,
    timeout_seconds: float = 5.0,
    capture_preview: bool = True,
    measure_fps: bool = False,
) -> ProbeResult:
    """Run the blocking probe in a worker thread with a hard deadline so the event loop never stalls."""
    hard_deadline = timeout_seconds * 2 + (3.0 if measure_fps else 1.0)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(probe_stream, rtsp_url, device_id, timeout_seconds, capture_preview, measure_fps),
            timeout=hard_deadline,
        )
    except asyncio.TimeoutError:
        return ProbeResult(False, f"Probe timed out after {hard_deadline:.0f}s", latency_ms=int(hard_deadline * 1000))
