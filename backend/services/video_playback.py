"""Browser-playable copies of video evidence.

Evidence files are never modified: their SHA-256 is part of the chain of custody. Clips recorded with the
MPEG-4 Part 2 codec (OpenCV "mp4v") cannot be decoded by Chrome/Edge, so playback uses a derived H.264
preview written next to the evidence store (evidence/previews). The preview carries the same frames at the
same frame rate; it is a viewing aid only and is never hashed, listed or served as evidence.
"""
import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from backend.services.evidence_service import evidence_service

logger = logging.getLogger("sih26187.playback")

BROWSER_CODECS = {"avc1", "h264", "x264", "vp80", "vp90", "av01"}

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def previews_dir() -> Path:
    return evidence_service.evidence_root / "previews"


def probe_codec(file_path: str) -> Tuple[str, float, int, int, int]:
    """(fourcc lower-case, fps, frame_count, width, height) of a video file."""
    import cv2

    capture = cv2.VideoCapture(file_path)
    try:
        code = int(capture.get(cv2.CAP_PROP_FOURCC) or 0)
        fourcc = "".join(chr((code >> 8 * i) & 0xFF) for i in range(4)).strip("\x00").lower()
        return (
            fourcc,
            float(capture.get(cv2.CAP_PROP_FPS) or 0),
            int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0),
        )
    finally:
        capture.release()


def _open_writer(path: Path, fps: float, size: Tuple[int, int]):
    """H.264 through Windows Media Foundation first (OpenCV's FFmpeg build has no H.264 encoder here),
    then H.264 through FFmpeg for other platforms."""
    import cv2

    fourcc = cv2.VideoWriter_fourcc(*"avc1")
    for api in (getattr(cv2, "CAP_MSMF", None), getattr(cv2, "CAP_FFMPEG", None)):
        if api is None:
            continue
        writer = cv2.VideoWriter(str(path), api, fourcc, fps, size)
        if writer.isOpened():
            return writer
        writer.release()
    return None


def _transcode(source: str, target: Path) -> None:
    import cv2

    _, fps, _, width, height = probe_codec(source)
    if width <= 0 or height <= 0:
        raise ValueError("Video has no readable frames")
    fps = fps if 0 < fps <= 240 else 25.0
    partial = target.with_suffix(".partial.mp4")
    writer = _open_writer(partial, fps, (width, height))
    if writer is None:
        raise RuntimeError("No H.264 encoder is available on this server")
    capture = cv2.VideoCapture(source)
    frames = 0
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height))
            writer.write(frame)
            frames += 1
    finally:
        capture.release()
        writer.release()
    if frames == 0 or not partial.is_file() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise ValueError("Video has no readable frames")
    os.replace(partial, target)
    logger.info("Playback preview written: %s (%d frames @ %.2f fps)", target.name, frames, fps)


def playable_path(evidence_id: int, file_path: str, file_hash: str) -> Tuple[str, bool]:
    """Path of a browser-playable file for this evidence and whether it is the original file."""
    codec, _, _, _, _ = probe_codec(file_path)
    if codec in BROWSER_CODECS:
        return file_path, True
    directory = previews_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # The hash in the name ties the preview to the exact evidence bytes it was derived from.
    target = directory / f"{evidence_id}_{file_hash[:16]}.mp4"
    with _lock_for(target.name):
        if not target.is_file():
            _transcode(file_path, target)
    return str(target), False


async def playable_path_async(evidence_id: int, file_path: str, file_hash: str) -> Tuple[str, bool]:
    return await asyncio.to_thread(playable_path, evidence_id, file_path, file_hash)


def video_duration_seconds(file_path: str) -> Optional[float]:
    _, fps, frames, _, _ = probe_codec(file_path)
    return frames / fps if fps > 0 and frames > 0 else None
