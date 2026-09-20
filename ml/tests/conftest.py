"""Shared fixtures for the ML test suite (no database; the backend is faked where needed)."""
import asyncio
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2  # noqa: E402
import httpx  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402

from ml.config import ml_config  # noqa: E402

# Local wall-clock instants, so night rules are deterministic whenever the suite runs.
DAY = datetime(2026, 9, 17, 12, 0, 0).astimezone()
NIGHT = datetime(2026, 9, 17, 23, 0, 0).astimezone()


@pytest.fixture
def evidence_dirs(tmp_path, monkeypatch):
    snapshots, clips = tmp_path / "snapshots", tmp_path / "clips"
    snapshots.mkdir()
    clips.mkdir()
    monkeypatch.setattr(ml_config, "evidence_path", str(tmp_path))
    monkeypatch.setattr(ml_config, "snapshots_path", str(snapshots))
    monkeypatch.setattr(ml_config, "clips_path", str(clips))
    return tmp_path


@pytest.fixture(scope="session")
def loaded_detector():
    from ml.detector import detector

    if not detector.loaded:
        assert detector.load(), "YOLOv8x failed to load"
    return detector


@pytest.fixture(scope="session")
def bus_image() -> np.ndarray:
    from ultralytics.utils import ASSETS

    image = cv2.imread(str(ASSETS / "bus.jpg"))
    assert image is not None, "ultralytics bus.jpg sample image missing"
    return image


def shifted_frames(image: np.ndarray, count: int, step_px: int = 3) -> List[np.ndarray]:
    """Simulate camera footage: the scene drifts a few pixels per frame."""
    height, width = image.shape[:2]
    frames = []
    for i in range(count):
        matrix = np.float32([[1, 0, i * step_px], [0, 1, 0]])
        frames.append(cv2.warpAffine(image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE))
    return frames


def write_video(path: Path, frames: List[np.ndarray], fps: float = 15.0) -> Path:
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    assert writer.isOpened()
    for frame in frames:
        writer.write(frame)
    writer.release()
    return path


class AdvancingClock:
    """Deterministic clock: each call moves time forward by one frame interval."""

    def __init__(self, start: datetime, step_seconds: float):
        self.current = start
        self.step = timedelta(seconds=step_seconds)

    def __call__(self) -> datetime:
        value = self.current
        self.current += self.step
        return value


# ---------------------------------------------------------------------------- fake backend

@dataclass
class FakeBackend:
    zones: List[Dict] = field(default_factory=list)
    cameras: List[Dict] = field(default_factory=list)
    logins: int = 0
    zone_requests: int = 0
    uploads: List[Dict] = field(default_factory=list)
    ws_messages: List[Dict] = field(default_factory=list)
    ws_connections: int = 0
    close_first_connection_after: Optional[int] = None
    fail_zone_requests: bool = False

    def http_handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/auth/login" and request.method == "POST":
            self.logins += 1
            return httpx.Response(200, json={
                "access_token": f"access-{self.logins}", "refresh_token": f"refresh-{self.logins}",
                "token_type": "bearer", "expires_in": 900,
                "user": {"username": "admin", "role": "admin"},
            })
        if request.headers.get("authorization", "").startswith("Bearer ") is False:
            return httpx.Response(401, json={"detail": "Not authenticated"})
        if path.startswith("/zones/") and request.method == "GET":
            self.zone_requests += 1
            if self.fail_zone_requests:
                return httpx.Response(503, json={"detail": "down"})
            return httpx.Response(200, json={"items": self.zones, "total": len(self.zones)})
        if path == "/cameras" and request.method == "GET":
            return httpx.Response(200, json={"items": self.cameras, "total": len(self.cameras)})
        if path == "/evidence/upload" and request.method == "POST":
            content = parse_multipart_file(request)
            digest = hashlib.sha256(content).hexdigest()
            self.uploads.append({"sha256": digest, "size": len(content)})
            return httpx.Response(201, json={"evidence_id": len(self.uploads), "file_hash": digest,
                                             "file_size_bytes": len(content)})
        return httpx.Response(404, json={"detail": f"no fake route for {request.method} {path}"})


def parse_multipart_file(request: httpx.Request) -> bytes:
    boundary = re.search(r"boundary=([^;]+)", request.headers["content-type"]).group(1).encode()
    body = request.read()
    for part in body.split(b"--" + boundary):
        if b'name="file"' in part:
            _, _, data = part.partition(b"\r\n\r\n")
            return data[:-2] if data.endswith(b"\r\n") else data
    raise AssertionError("no file part in upload")


@pytest.fixture
def fake_backend():
    return FakeBackend()


@pytest.fixture
def backend_client(fake_backend):
    from ml.backend_client import BackendClient

    return BackendClient(base_url="http://fake-backend", username="admin", password="secret",
                         transport=httpx.MockTransport(fake_backend.http_handler))


@pytest_asyncio.fixture
async def ws_server(fake_backend):
    import websockets

    async def handler(connection, path=None):
        fake_backend.ws_connections += 1
        connection_number = fake_backend.ws_connections
        await connection.send(json.dumps({"type": "connected", "may_ingest_frames": True, "role": "admin"}))
        received = 0
        try:
            async for raw in connection:
                message = json.loads(raw)
                fake_backend.ws_messages.append(message)
                received += 1
                if message.get("type") == "frame_data":
                    await connection.send(json.dumps({
                        "type": "frame_ack", "detections_saved": len(message.get("detections", [])),
                        "alerts_created": len(message.get("alerts", [])), "problems": [],
                    }))
                if (connection_number == 1 and fake_backend.close_first_connection_after
                        and received >= fake_backend.close_first_connection_after):
                    await connection.close()
                    return
        except websockets.ConnectionClosed:
            return

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}"
    server.close()
    await server.wait_closed()
