"""Test harness. All tests run against throwaway databases, never the real sih26187 database."""
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

URL_PATTERN = re.compile(r"^\w+(?:\+\w+)?://(?P<user>[^:/@]+):(?P<password>[^@/]*)@(?P<host>[^:/]+):(?P<port>\d+)/")


def _postgres_server():
    """Host, port and credentials of the PostgreSQL server, taken from .env.

    Never hard-coded: this file is public, and the tests must run against whatever server the developer
    configured. TEST_DATABASE_URL_SYNC overrides it (e.g. for CI)."""
    url = os.environ.get("TEST_DATABASE_URL_SYNC", "")
    env_file = PROJECT_ROOT / ".env"
    if not url and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("DATABASE_URL_SYNC="):
                url = line.split("=", 1)[1].strip()
                break
    match = URL_PATTERN.match(url)
    if not match:
        raise RuntimeError(
            "Cannot read the PostgreSQL server from DATABASE_URL_SYNC in .env "
            "(or TEST_DATABASE_URL_SYNC). Copy .env.example to .env and fill it in."
        )
    return match["host"], int(match["port"]), match["user"], match["password"]


PG_HOST, PG_PORT, PG_USER, PG_PASSWORD = _postgres_server()
TEST_DB = "sih26187_test"
MIGRATION_TEST_DB = "sih26187_migtest"


def async_url(db_name: str) -> str:
    return f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{db_name}"


def sync_url(db_name: str) -> str:
    return f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{db_name}"


# Must be set before any backend import: environment variables override the .env file.
os.environ["DATABASE_URL"] = async_url(TEST_DB)
os.environ["DATABASE_URL_SYNC"] = sync_url(TEST_DB)
os.environ["ENVIRONMENT"] = "test"
os.environ["DB_ECHO"] = "False"

import psycopg2  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from backend.core.enums import ZoneRegion  # noqa: E402
from backend.database.database import Base  # noqa: E402
from backend.models import Camera, User  # noqa: E402
from backend.core.security import hash_password  # noqa: E402

assert TEST_DB in os.environ["DATABASE_URL"], "Refusing to run tests against a non-test database"


def admin_connection():
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname="postgres")
    conn.autocommit = True
    return conn


def recreate_database(name: str) -> None:
    assert name.endswith(("_test", "_migtest")), "only throwaway databases may be recreated"
    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            cur.execute(f"CREATE DATABASE \"{name}\" ENCODING 'UTF8' TEMPLATE template0")
    finally:
        conn.close()


def drop_database(name: str) -> None:
    assert name.endswith(("_test", "_migtest")), "only throwaway databases may be dropped"
    conn = admin_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def test_database():
    recreate_database(TEST_DB)
    engine = create_engine(sync_url(TEST_DB), poolclass=NullPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))
    Base.metadata.create_all(engine)
    engine.dispose()
    yield TEST_DB
    drop_database(TEST_DB)


@pytest_asyncio.fixture
async def db(test_database):
    """Session inside an outer transaction that is always rolled back (tests never leak rows)."""
    engine = create_async_engine(async_url(TEST_DB), poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def camera(db) -> Camera:
    cam = Camera(
        camera_id="CAM-N01",
        name="North Fence Tower 1",
        location_name="Raxaul Sector Post 4",
        gps_lat="26.98123456",
        gps_lng="84.85123456",
        zone_region=ZoneRegion.NORTH,
        sector_name="Raxaul",
    )
    db.add(cam)
    await db.flush()
    return cam


STRONG_PASSWORD = "Border#Secure2026"


@pytest_asyncio.fixture
async def operator(db) -> User:
    user = User(
        username="operator1",
        password_hash=hash_password(STRONG_PASSWORD),
        role="operator",
        camera_access=["CAM-N01"],
        zone_access=["north"],
    )
    db.add(user)
    await db.flush()
    return user
