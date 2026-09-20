"""Launch the ML pipeline for every registered camera, in parallel.

    C:\\pythonjarvis\\python.exe -m ml.main                          # all registered cameras
    C:\\pythonjarvis\\python.exe -m ml.main --camera CAM-N-001=0     # explicit source
    C:\\pythonjarvis\\python.exe -m ml.main --duration 60            # bounded run
"""
import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ml.backend_client import BackendAuthError, backend_client  # noqa: E402
from ml.config import _env, ml_config  # noqa: E402
from ml.detector import detector  # noqa: E402
from ml.pipeline import Pipeline  # noqa: E402

logger = logging.getLogger("sih26187.ml.main")

WEBCAM_REGISTRATION = {
    "name": "Local Webcam (device 0)",
    "location_name": "Operator workstation",
    "device_id": "0",
    "camera_type": "standard",
    "zone_region": "north",
    "sector_name": "Local Test",
}


def configure_logging(level: str) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    file_handler = RotatingFileHandler(Path(ml_config.logs_path) / "ml_pipeline.log", maxBytes=20 * 1024 * 1024,
                                       backupCount=5, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.handlers[:] = [console, file_handler]
    for noisy in ("httpx", "httpcore", "websockets", "ultralytics"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def get_token() -> str:
    return await backend_client.get_token()


async def get_registered_cameras(token: Optional[str] = None) -> List[Dict]:
    return await backend_client.list_cameras()


def unmasked_stream_url(camera_id: str) -> Optional[str]:
    """The API masks stream passwords (rtsp://user:***@host). The pipeline is a trusted local service,
    so it reads the real URL read-only from the database."""
    import psycopg2

    dsn = _env("DATABASE_URL_SYNC", "")
    if not dsn:
        return None
    parts = urlsplit(dsn.replace("postgresql+psycopg2://", "postgresql://"))
    connection = psycopg2.connect(host=parts.hostname, port=parts.port or 5432, user=parts.username,
                                  password=parts.password, dbname=parts.path.lstrip("/"))
    try:
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("SELECT rtsp_url FROM cameras WHERE camera_id = %s", (camera_id,))
            row = cursor.fetchone()
            return row[0] if row else None
    finally:
        connection.close()


def resolve_source(camera: Dict):
    device_id = camera.get("device_id")
    if device_id is not None and str(device_id).strip().isdigit():
        return int(str(device_id).strip())
    url = camera.get("rtsp_url")
    if url and "***" in url:
        url = unmasked_stream_url(camera["camera_id"])
    return url or None


def parse_camera_args(values: List[str]) -> List[Dict]:
    cameras = []
    for value in values:
        if "=" not in value:
            raise SystemExit(f"--camera expects CAMERA_ID=SOURCE, got {value!r}")
        camera_id, source = value.split("=", 1)
        cameras.append({"camera_id": camera_id.strip().upper(), "source": source.strip()})
    return cameras


async def discover_cameras(explicit: List[Dict], register_webcam: bool) -> List[Dict]:
    if explicit:
        return explicit
    registered = await get_registered_cameras()
    cameras = []
    for camera in registered:
        source = resolve_source(camera)
        if source is None:
            logger.info("Skipping %s: no stream source configured", camera["camera_id"])
            continue
        cameras.append({"camera_id": camera["camera_id"], "source": source})

    if not cameras and register_webcam:
        logger.warning("No cameras with a stream source are registered — registering the local webcam (device 0)")
        created = await backend_client.register_camera(WEBCAM_REGISTRATION)
        logger.info("Registered %s (status=%s): %s", created["camera_id"], created["status"], created["message"])
        cameras.append({"camera_id": created["camera_id"], "source": 0})
    return cameras[: ml_config.max_cameras]


async def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SIH26187 ML pipeline")
    parser.add_argument("--camera", action="append", default=[], help="CAMERA_ID=SOURCE (webcam index, RTSP URL or file)")
    parser.add_argument("--duration", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--no-register-webcam", action="store_true", help="do not auto-register device 0")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    configure_logging(args.log_level)
    logger.info("SIH26187 ML Pipeline starting...")
    logger.info("Loading %s on %s...", Path(ml_config.model_path).name, ml_config.device)
    if not await asyncio.to_thread(detector.load):
        logger.error("ERROR: YOLOv8x model failed to load")
        return 2

    try:
        await get_token()
    except BackendAuthError as exc:
        # Never retry credentials in a loop: three failures lock the account for 30 minutes.
        logger.error("Backend login failed: %s", exc)
        return 3
    except Exception as exc:
        logger.error("Backend unreachable at %s: %s", ml_config.backend_url, exc)
        return 4

    cameras = await discover_cameras(parse_camera_args(args.camera), register_webcam=not args.no_register_webcam)
    if not cameras:
        logger.error("No cameras to process")
        return 5

    logger.info("Starting pipelines for %d camera(s)...", len(cameras))
    pipelines = [Pipeline(cam["camera_id"], cam["source"]) for cam in cameras]
    tasks = []
    for pipeline in pipelines:
        tasks.append(asyncio.create_task(pipeline.run(duration=args.duration), name=f"pipeline-{pipeline.camera_id}"))
        logger.info("Pipeline started: %s — source: %s", pipeline.camera_id, pipeline.source)

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutting down pipelines...")
        for pipeline in pipelines:
            pipeline.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
    return 0


def run() -> None:
    try:
        exit_code = asyncio.run(main())
    except KeyboardInterrupt:
        exit_code = 0
        print("\nML pipeline stopped by operator")
    sys.exit(exit_code)


if __name__ == "__main__":
    run()
