"""Camera / video source reader with automatic reconnection.

Live sources (webcam index, RTSP/HTTP URL) run a background grabber thread that always holds the newest
frame, so a slow detector never processes a backlog of stale frames. Video files are read sequentially
without dropping frames (uploaded evidence must be analysed completely).
"""
import base64
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional, Tuple, Union

import cv2
import numpy as np

from ml.config import ml_config
from ml.night import ensure_bgr

logger = logging.getLogger("sih26187.ml.stream")

DEFAULT_FPS = 30.0
OPEN_TIMEOUT_MS = 10000
READ_TIMEOUT_MS = 10000


def parse_source(source: Union[int, str, None]) -> Union[int, str]:
    """'0' -> 0 (webcam index); anything else unchanged."""
    if isinstance(source, str) and source.strip().isdigit():
        return int(source.strip())
    return source


class CameraStream:
    def __init__(self, source, camera_id: str = "unknown", realtime: Optional[bool] = None,
                 max_reconnect_attempts: Optional[int] = None):
        self.source = parse_source(source)  # int for webcam, str for rtsp/http/file
        self.camera_id = camera_id
        self.is_file = isinstance(self.source, str) and "://" not in self.source and Path(self.source).exists()
        self.realtime = (not self.is_file) if realtime is None else realtime
        self.cap: Optional[cv2.VideoCapture] = None
        self._alive = False
        self._lock = threading.Lock()
        self._frame_ready = threading.Condition(self._lock)
        self._reconnect_delay = 5  # seconds, first retry
        self._max_reconnect_delay = 30
        self._reconnect_attempts = 0
        # None = retry forever: a border camera must come back without an operator restarting the pipeline.
        self._max_reconnect_attempts = max_reconnect_attempts
        self._latest: Optional[np.ndarray] = None
        self._latest_index = 0
        self._consumed_index = 0
        self._grabber: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.fps = 0.0
        #: Frames in the file (0 for live sources, which have no end).
        self.resolution: Tuple[int, int] = (0, 0)
        self.frame_count = 0
        self.frames_read = 0
        self.reconnects = 0
        self.ended = False  # video file finished

    # ------------------------------------------------------------------ connection

    def start(self) -> bool:
        success = self._connect()
        self._alive = success
        if success and self.realtime:
            self._stop.clear()
            self._grabber = threading.Thread(target=self._grab_loop, name=f"grab-{self.camera_id}", daemon=True)
            self._grabber.start()
        elif not success and self.realtime:
            # Keep trying in the background; read() returns None until the camera comes up.
            self._alive = True
            self._stop.clear()
            self._grabber = threading.Thread(target=self._grab_loop, name=f"grab-{self.camera_id}", daemon=True)
            self._grabber.start()
        return success

    def _open_capture(self) -> cv2.VideoCapture:
        if isinstance(self.source, int):
            backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
            return cv2.VideoCapture(self.source, backend)
        if self.is_file:
            return cv2.VideoCapture(self.source)
        params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, OPEN_TIMEOUT_MS, cv2.CAP_PROP_READ_TIMEOUT_MSEC, READ_TIMEOUT_MS]
        return cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, params)

    def _connect(self) -> bool:
        try:
            if self.cap is not None:
                self.cap.release()
            self.cap = self._open_capture()
            if not self.cap.isOpened():
                return False
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if isinstance(self.source, int):
                self.cap.set(cv2.CAP_PROP_FPS, ml_config.target_fps)
                if ml_config.camera_exposure is not None:
                    self.cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)  # DirectShow: 0.25 = manual
                    self.cap.set(cv2.CAP_PROP_EXPOSURE, ml_config.camera_exposure)
            fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0.0)
            self.fps = fps if 1.0 <= fps <= 240.0 else DEFAULT_FPS
            self.resolution = (int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) if self.is_file else 0
            self.frame_count = count if count > 0 else 0
            self._reconnect_attempts = 0
            logger.info("[%s] source opened %sx%s @ %.1f fps", self.camera_id, *self.resolution, self.fps)
            return True
        except Exception as exc:
            logger.warning("[%s] connection failed: %s", self.camera_id, exc)
            return False

    def _next_backoff(self) -> float:
        return min(self._reconnect_delay * (2 ** min(self._reconnect_attempts, 4)), self._max_reconnect_delay)

    def _attempt_reconnect(self) -> bool:
        if self._max_reconnect_attempts is not None and self._reconnect_attempts >= self._max_reconnect_attempts:
            logger.error("[%s] giving up after %d reconnect attempts", self.camera_id, self._reconnect_attempts)
            self._alive = False
            return False
        delay = self._next_backoff()
        self._reconnect_attempts += 1
        logger.warning("[%s] stream lost, reconnect attempt %d in %.0fs", self.camera_id, self._reconnect_attempts, delay)
        if self._stop.wait(delay):
            return False
        if self._connect():
            self.reconnects += 1
            logger.info("[%s] reconnected", self.camera_id)
            return True
        return False

    # ------------------------------------------------------------------ reading

    def _grab_loop(self) -> None:
        while not self._stop.is_set():
            if self.cap is None or not self.cap.isOpened():
                if not self._attempt_reconnect():
                    if not self._alive:
                        return
                    continue
            ok, frame = self.cap.read()
            if not ok or frame is None:
                if not self._attempt_reconnect() and not self._alive:
                    return
                continue
            frame = ensure_bgr(frame)
            with self._frame_ready:
                self._latest = frame
                self._latest_index += 1
                self.frames_read += 1
                self._frame_ready.notify_all()

    def read(self, timeout: float = 1.0) -> Optional[np.ndarray]:
        if self.realtime:
            with self._frame_ready:
                if self._latest_index == self._consumed_index:
                    self._frame_ready.wait(timeout)
                if self._latest is None or self._latest_index == self._consumed_index:
                    return None
                self._consumed_index = self._latest_index
                return self._latest

        # Sequential file / non-realtime reading.
        if self.cap is None or not self.cap.isOpened():
            if self.is_file or not self._attempt_reconnect():
                return None
        with self._lock:
            ok, frame = self.cap.read()
        if not ok or frame is None:
            if self.is_file:
                self.ended = True
                self._alive = False
                return None
            self._attempt_reconnect()
            return None
        self.frames_read += 1
        return ensure_bgr(frame)

    def position_seconds(self) -> float:
        """Media timestamp of the last frame read (files); frame count / fps as a fallback."""
        if self.cap is not None and self.is_file:
            msec = self.cap.get(cv2.CAP_PROP_POS_MSEC)
            if msec and msec > 0:
                return msec / 1000.0
        return self.frames_read / (self.fps or DEFAULT_FPS)

    def is_alive(self) -> bool:
        return self._alive

    def get_frame_preview(self, quality: int = 85) -> Optional[str]:
        frame = self.read()
        if frame is None:
            return None
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return base64.b64encode(buffer.tobytes()).decode("ascii") if ok else None

    def release(self) -> None:
        self._alive = False
        self._stop.set()
        with self._frame_ready:
            self._frame_ready.notify_all()
        if self._grabber is not None and self._grabber.is_alive():
            self._grabber.join(timeout=3)
        if self.cap is not None:
            self.cap.release()
            self.cap = None
