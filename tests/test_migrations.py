"""The Alembic migrations must build exactly the schema the ORM models describe."""
import subprocess
import sys

import psycopg2
import pytest

from tests.conftest import (
    MIGRATION_TEST_DB,
    PG_HOST,
    PG_PASSWORD,
    PG_PORT,
    PG_USER,
    PROJECT_ROOT,
    TEST_DB,
    drop_database,
    recreate_database,
    sync_url,
)

SNAPSHOT_QUERIES = {
    "columns": """
        SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default,
               character_maximum_length, numeric_precision, numeric_scale
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name <> 'alembic_version'
        ORDER BY table_name, column_name
    """,
    "indexes": """
        SELECT tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname = 'public' AND tablename <> 'alembic_version'
        ORDER BY tablename, indexname
    """,
    "constraints": """
        SELECT rel.relname, con.conname, con.contype, pg_get_constraintdef(con.oid)
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        WHERE ns.nspname = 'public' AND rel.relname <> 'alembic_version'
        ORDER BY rel.relname, con.conname
    """,
    "enums": """
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder)
        FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid
        GROUP BY t.typname ORDER BY t.typname
    """,
}


def snapshot(db_name: str) -> dict:
    conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASSWORD, dbname=db_name)
    try:
        with conn.cursor() as cur:
            result = {}
            for key, query in SNAPSHOT_QUERIES.items():
                cur.execute(query)
                result[key] = cur.fetchall()
            return result
    finally:
        conn.close()


def alembic(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"dburl={sync_url(MIGRATION_TEST_DB)}", *args],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture(scope="module")
def migrated_db():
    recreate_database(MIGRATION_TEST_DB)
    result = alembic("upgrade", "head")
    assert result.returncode == 0, result.stderr
    yield MIGRATION_TEST_DB
    drop_database(MIGRATION_TEST_DB)


def test_migration_matches_orm_schema_exactly(migrated_db, test_database):
    migrated, orm = snapshot(migrated_db), snapshot(TEST_DB)
    for key in SNAPSHOT_QUERIES:
        assert migrated[key] == orm[key], f"{key} differ between the migrations and ORM metadata"
    assert len(migrated["enums"]) == 10
    assert len({row[0] for row in migrated["columns"]}) == 10
    # 48 from V001 + the partial idx_zones_geo added by V002.
    assert sum(1 for row in migrated["indexes"] if row[1].startswith("idx_")) == 49
    fk_rules = {row[1]: row[3] for row in migrated["constraints"] if row[2] == "f"}
    assert "ON DELETE CASCADE" in fk_rules["fk_zones_camera_id_cameras"]
    assert "ON DELETE SET NULL" in fk_rules["fk_alerts_acknowledged_by_users"]
    assert "ON DELETE RESTRICT" in fk_rules["fk_evidence_alert_id_alerts"]
    assert "ON DELETE SET NULL" in fk_rules["fk_hardware_registry_camera_id_cameras"]


def test_alembic_check_reports_no_drift(migrated_db):
    result = alembic("check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new upgrade operations detected" in (result.stdout + result.stderr)


def test_downgrade_and_reupgrade(migrated_db):
    down = alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr
    empty = snapshot(migrated_db)
    assert empty["columns"] == [] and empty["enums"] == []

    up = alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr
    assert snapshot(migrated_db) == snapshot(TEST_DB)
    current = alembic("current")
    assert "V002 (head)" in current.stdout
