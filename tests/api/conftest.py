"""Fixtures for the FastAPI layer. Everything runs against sih26187_test with throwaway evidence storage."""
import asyncio
from pathlib import Path
from typing import Dict, List, Optional

import psycopg2
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.core.api_config import api_settings
from backend.core.auth import create_access_token, token_blacklist, token_claims_for
from backend.core.config import settings
from backend.core.enums import UserRole
from backend.core.rate_limit import limiter
from backend.core.runtime import runtime_state
from backend.core.security import hash_password
from backend.database import database as database_module
from backend.models import User
from backend.websocket.manager import manager

from tests.conftest import PG_HOST, PG_PASSWORD, PG_PORT, PG_USER, TEST_DB, async_url

TABLES = (
    "tracked_objects", "evidence", "alerts", "events", "zones",
    "hardware_registry", "system_health", "audit_logs", "cameras", "users",
)

TEST_PASSWORD = "Border#Secure2026"


@pytest.fixture(scope="session", autouse=True)
def api_environment(test_database, tmp_path_factory):
    """Point the global engine at the test database with NullPool, and evidence storage at a temp dir."""
    evidence_root = tmp_path_factory.mktemp("evidence")
    for name in ("snapshots", "clips", "uploads", "cold_storage"):
        (evidence_root / name).mkdir()

    patcher = pytest.MonkeyPatch()
    patcher.setattr(settings, "EVIDENCE_PATH", str(evidence_root), raising=False)
    patcher.setattr(settings, "SNAPSHOTS_PATH", str(evidence_root / "snapshots"), raising=False)
    patcher.setattr(settings, "CLIPS_PATH", str(evidence_root / "clips"), raising=False)

    engine = create_async_engine(async_url(TEST_DB), poolclass=NullPool)
    patcher.setattr(database_module, "engine", engine)
    patcher.setattr(
        database_module,
        "AsyncSessionLocal",
        async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False),
    )
    # The lifespan runs for the WebSocket TestClient; the monitor must not probe real devices there.
    patcher.setattr(api_settings, "CAMERA_MONITOR_ENABLED", False, raising=False)
    limiter.enabled = False  # re-enabled only by the rate limit test
    yield evidence_root
    patcher.undo()
    asyncio.run(engine.dispose())


def _truncate_tables() -> None:
    connection = psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=TEST_DB)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    connection.close()


@pytest.fixture(autouse=True)
def clean_tables(api_environment):
    """API endpoints commit, so every test starts from empty tables — and leaves them empty, so the
    database-layer tests that share this database never inherit rows (e.g. usernames) from the last API test."""
    _truncate_tables()
    runtime_state.reset()
    token_blacklist.clear()
    manager.camera_rooms.clear()
    manager.operator_connections.clear()
    manager._socket_owner.clear()
    yield
    _truncate_tables()


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    async with database_module.AsyncSessionLocal() as db:
        yield db


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    from backend.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http_client:
        yield http_client


async def make_user(
    db: AsyncSession,
    username: str,
    role: UserRole,
    camera_access: Optional[List[str]] = None,
    zone_access: Optional[List[str]] = None,
    password: str = TEST_PASSWORD,
    is_active: bool = True,
) -> User:
    user = User(
        username=username,
        password_hash=hash_password(password),
        role=role,
        camera_access=camera_access or [],
        zone_access=zone_access or [],
        is_active=is_active,
    )
    db.add(user)
    await db.commit()
    return user


def auth_header(user: User) -> Dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(token_claims_for(user))}"}


@pytest_asyncio.fixture
async def admin(session) -> User:
    return await make_user(session, "admin", UserRole.ADMIN, zone_access=["north", "south", "east", "west"])


@pytest_asyncio.fixture
async def supervisor(session) -> User:
    return await make_user(session, "supervisor.north", UserRole.SUPERVISOR, zone_access=["north"])


@pytest_asyncio.fixture
async def operator(session) -> User:
    return await make_user(session, "operator.north", UserRole.OPERATOR, camera_access=["CAM-N-001"])


@pytest_asyncio.fixture
async def outsider(session) -> User:
    """Operator with no camera or zone grants at all."""
    return await make_user(session, "operator.south", UserRole.OPERATOR, zone_access=["south"])


@pytest.fixture
def admin_headers(admin) -> Dict[str, str]:
    return auth_header(admin)


@pytest.fixture
def supervisor_headers(supervisor) -> Dict[str, str]:
    return auth_header(supervisor)


@pytest.fixture
def operator_headers(operator) -> Dict[str, str]:
    return auth_header(operator)


@pytest.fixture
def fake_probe(monkeypatch):
    """Replace the OpenCV probe so tests never touch a webcam or the network."""
    from backend.services import stream_probe as probe_module

    calls = []

    def configure(connected: bool = True, message: str = "Stream connected", fps: float = 25.0):
        async def _probe(rtsp_url=None, device_id=None, timeout_seconds=5.0, capture_preview=True, measure_fps=False):
            calls.append({"rtsp_url": rtsp_url, "device_id": device_id})
            return probe_module.ProbeResult(
                connected=connected,
                message=message,
                fps=fps if connected else 0.0,
                width=1920 if connected else 0,
                height=1080 if connected else 0,
                frame_jpeg_b64="/9j/FAKEPREVIEW" if (connected and capture_preview) else None,
                latency_ms=42,
            )

        for module in ("backend.routers.cameras", "backend.services.camera_monitor", "backend.routers.hardware"):
            monkeypatch.setattr(f"{module}.probe_stream_async", _probe)
        return calls

    configure.calls = calls
    return configure


@pytest_asyncio.fixture
async def camera(client, supervisor_headers, fake_probe) -> dict:
    fake_probe(connected=True)
    response = await client.post(
        "/cameras/register",
        headers=supervisor_headers,
        json={
            "name": "North Fence Tower 1",
            "zone_region": "north",
            "sector_name": "Raxaul",
            "rtsp_url": "rtsp://10.0.0.21:554/stream1",
            "gps_lat": 26.98123456,
            "gps_lng": 84.85123456,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- synchronous helpers
# The WebSocket tests drive Starlette's TestClient, which is synchronous, so they cannot consume
# the async fixtures above. These build the same rows through psycopg2 instead.

@pytest.fixture
def sync_conn(api_environment):
    connection = psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=TEST_DB)
    connection.autocommit = True
    yield connection
    connection.close()


@pytest.fixture
def sync_db(sync_conn):
    def run(sql: str, params=None):
        with sync_conn.cursor() as cursor:
            cursor.execute(sql, params or ())
            return cursor.fetchall() if cursor.description else []

    return run


def _insert_user(connection, username: str, role: UserRole, camera_access=None, zone_access=None) -> User:
    import json
    import uuid as uuid_module

    user_id = uuid_module.uuid4()
    camera_access = camera_access or []
    zone_access = zone_access or []
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO users (user_id, username, password_hash, role, camera_access, zone_access, is_active)"
            " VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, true)",
            (str(user_id), username, hash_password(TEST_PASSWORD), role.value,
             json.dumps(camera_access), json.dumps(zone_access)),
        )
    return User(user_id=user_id, username=username, role=role,
                camera_access=camera_access, zone_access=zone_access, is_active=True)


@pytest.fixture
def ws_admin(sync_conn) -> User:
    return _insert_user(sync_conn, "admin", UserRole.ADMIN, zone_access=["north", "south", "east", "west"])


@pytest.fixture
def ws_supervisor(sync_conn) -> User:
    return _insert_user(sync_conn, "supervisor.north", UserRole.SUPERVISOR, zone_access=["north"])


@pytest.fixture
def ws_operator(sync_conn) -> User:
    return _insert_user(sync_conn, "operator.north", UserRole.OPERATOR, camera_access=["CAM-N-001"])


@pytest.fixture
def ws_outsider(sync_conn) -> User:
    return _insert_user(sync_conn, "operator.south", UserRole.OPERATOR, zone_access=["south"])


@pytest.fixture
def ws_camera(sync_conn) -> str:
    with sync_conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO cameras (camera_id, name, zone_region, sector_name, status, rtsp_url)"
            " VALUES ('CAM-N-001', 'North Fence Tower 1', 'north', 'Raxaul', 'online', 'rtsp://10.0.0.21:554/s')"
        )
    return "CAM-N-001"


def jpeg_bytes(width: int = 64, height: int = 48) -> bytes:
    import cv2
    import numpy as np

    ok, buffer = cv2.imencode(".jpg", np.zeros((height, width, 3), np.uint8))
    assert ok
    return buffer.tobytes()
