import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402
from sqlalchemy import text  # noqa: E402

from backend.core.config import settings  # noqa: E402
from backend.database.database import Base, check_db_connection, enable_extensions, engine  # noqa: E402
from backend.models import *  # noqa: E402,F401,F403

EXPECTED_TABLE_COUNT = 10


def _current_revision(sync_conn):
    return MigrationContext.configure(sync_conn).get_current_revision()


def _stamp_alembic_head() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "backend" / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.DATABASE_URL_SYNC)
    command.stamp(config, "head")


async def init_database() -> None:
    print("Initializing SIH26187 database...")
    if not await check_db_connection():
        raise SystemExit("Cannot connect to PostgreSQL - check DATABASE_URL in .env")

    settings.ensure_directories()
    print("Storage directories verified")

    await enable_extensions()
    print("pgcrypto + uuid-ossp extensions enabled")

    async with engine.begin() as conn:
        revision_before = await conn.run_sync(_current_revision)
        await conn.run_sync(Base.metadata.create_all)
    print(f"All {len(Base.metadata.tables)} tables created successfully")

    if revision_before is None:
        # Schema came from create_all, which is identical to migration V001: record that fact.
        _stamp_alembic_head()
        print("Alembic version stamped at head (V001)")
    else:
        print(f"Alembic already at revision {revision_before} - not re-stamping")

    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "AND table_name <> 'alembic_version' ORDER BY table_name"
            )
        )
        tables = [row[0] for row in rows]
        index_count = (
            await conn.execute(
                text("SELECT count(*) FROM pg_indexes WHERE schemaname = 'public' AND indexname LIKE 'idx_%'")
            )
        ).scalar()
    print(f"Verified tables ({len(tables)}): {', '.join(tables)}")
    print(f"Verified named indexes: {index_count}")
    if len(tables) < EXPECTED_TABLE_COUNT:
        raise SystemExit(f"Expected {EXPECTED_TABLE_COUNT} tables, found {len(tables)}")
    print("Database initialization complete")


async def main() -> None:
    try:
        await init_database()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
