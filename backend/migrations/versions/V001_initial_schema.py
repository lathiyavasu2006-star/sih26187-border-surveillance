"""V001 initial schema: 10 enums, 10 tables, 48 named indexes, FK + CHECK constraints.

Revision ID: V001
Revises:
Create Date: 2026-09-15 00:00:00+05:30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "V001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ENUMS = {
    "camera_type_enum": ("standard", "ptz", "thermal", "drone", "radar", "scanner", "satellite"),
    "zone_region_enum": ("north", "south", "east", "west"),
    "camera_status_enum": ("online", "offline", "degraded"),
    "user_role_enum": ("admin", "regional_head", "supervisor", "operator"),
    "zone_type_enum": ("public", "buffer", "sensitive", "restricted", "no_mans_land"),
    "alert_type_enum": (
        "intrusion",
        "loitering",
        "weapon",
        "vehicle",
        "behavior",
        "camera_offline",
        "zone_breach",
        "animal",
        "smoke",
        "fence_damage",
    ),
    "risk_level_enum": ("normal", "low", "suspicious", "high_risk", "critical"),
    "evidence_type_enum": ("snapshot", "video_clip", "manual_snapshot", "uploaded_video"),
    "hardware_type_enum": (
        "standard_camera",
        "ptz_camera",
        "thermal_camera",
        "drone",
        "radar",
        "license_plate_scanner",
        "satellite",
    ),
    "hardware_status_enum": ("connected", "disconnected", "error", "standby"),
}

# Tables in dependency order (cameras first, users second); dropped in reverse.
TABLES = (
    "cameras",
    "users",
    "zones",
    "alerts",
    "events",
    "tracked_objects",
    "evidence",
    "hardware_registry",
    "audit_logs",
    "system_health",
)

NOW = sa.text("now()")
EMPTY_ARRAY = sa.text("'[]'::jsonb")
EMPTY_OBJECT = sa.text("'{}'::jsonb")
TRUE = sa.text("true")
FALSE = sa.text("false")
ZERO = sa.text("0")


def enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(*ENUMS[name], name=name, create_type=False)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # ---- 1. ENUM types ----
    for name, values in ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)

    # ---- 2. cameras ----
    op.create_table(
        "cameras",
        sa.Column("camera_id", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("location_name", sa.String(200), nullable=True),
        sa.Column("gps_lat", sa.Numeric(10, 8), nullable=True),
        sa.Column("gps_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("rtsp_url", sa.Text(), nullable=True),
        sa.Column("device_id", sa.String(100), nullable=True),
        sa.Column("camera_type", enum("camera_type_enum"), nullable=False, server_default=sa.text("'standard'")),
        sa.Column("zone_region", enum("zone_region_enum"), nullable=False),
        sa.Column("sector_name", sa.String(100), nullable=True),
        sa.Column("status", enum("camera_status_enum"), nullable=False, server_default=sa.text("'online'")),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("camera_id", name="pk_cameras"),
        sa.CheckConstraint("gps_lat IS NULL OR (gps_lat >= -90 AND gps_lat <= 90)", name=op.f("ck_cameras_gps_lat_range")),
        sa.CheckConstraint(
            "gps_lng IS NULL OR (gps_lng >= -180 AND gps_lng <= 180)", name=op.f("ck_cameras_gps_lng_range")
        ),
        sa.CheckConstraint("char_length(camera_id) >= 2", name=op.f("ck_cameras_camera_id_length")),
    )
    op.create_index("idx_cameras_status", "cameras", ["status"])
    op.create_index("idx_cameras_zone_region", "cameras", ["zone_region"])
    op.create_index("idx_cameras_type", "cameras", ["camera_type"])
    op.create_index("idx_cameras_last_seen", "cameras", ["last_seen"])
    op.create_index("idx_cameras_region_status", "cameras", ["zone_region", "status"])

    # ---- 3. users ----
    op.create_table(
        "users",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(50), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", enum("user_role_enum"), nullable=False),
        sa.Column("camera_access", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("zone_access", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=TRUE),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name="pk_users"),
        sa.CheckConstraint("failed_login_attempts >= 0", name=op.f("ck_users_failed_login_attempts_non_negative")),
        sa.CheckConstraint("char_length(username) >= 3", name=op.f("ck_users_username_length")),
        sa.CheckConstraint("password_hash LIKE '$2%'", name=op.f("ck_users_password_hash_is_bcrypt")),
    )
    op.create_index("idx_users_username", "users", ["username"], unique=True)
    op.create_index("idx_users_role", "users", ["role"])
    op.create_index("idx_users_active", "users", ["is_active"])

    # ---- 4. zones ----
    op.create_table(
        "zones",
        sa.Column("zone_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("camera_id", sa.String(20), nullable=False),
        sa.Column("zone_name", sa.String(100), nullable=False),
        sa.Column("zone_type", enum("zone_type_enum"), nullable=False),
        sa.Column("polygon", postgresql.JSONB(), nullable=False),
        sa.Column("loiter_threshold_seconds", sa.Integer(), nullable=False, server_default=sa.text("30")),
        sa.Column("risk_bonus", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column(
            "night_rules",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("""'{"multiplier": 1.5, "start": 22, "end": 5}'::jsonb"""),
        ),
        sa.Column("allowed_persons", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("color_hex", sa.String(7), nullable=False, server_default=sa.text("'#00ff00'")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=TRUE),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("zone_id", name="pk_zones"),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.camera_id"], name="fk_zones_camera_id_cameras", ondelete="CASCADE"
        ),
        sa.CheckConstraint("loiter_threshold_seconds > 0", name=op.f("ck_zones_loiter_threshold_positive")),
        sa.CheckConstraint("risk_bonus >= 0 AND risk_bonus <= 100", name=op.f("ck_zones_risk_bonus_range")),
        sa.CheckConstraint("color_hex ~ '^#[0-9A-Fa-f]{6}$'", name=op.f("ck_zones_color_hex_format")),
        sa.CheckConstraint("jsonb_typeof(polygon) = 'array'", name=op.f("ck_zones_polygon_is_array")),
    )
    op.create_index("idx_zones_camera_id", "zones", ["camera_id"])
    op.create_index("idx_zones_type", "zones", ["zone_type"])
    op.create_index("idx_zones_active", "zones", ["is_active"])
    op.create_index("idx_zones_camera_type", "zones", ["camera_id", "zone_type"])

    # ---- 5. alerts ----
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.String(40), nullable=False),
        sa.Column("camera_id", sa.String(20), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("person_uuid", sa.String(64), nullable=True),
        sa.Column("alert_type", enum("alert_type_enum"), nullable=False),
        sa.Column("risk_score", sa.Integer(), nullable=False),
        sa.Column("risk_level", enum("risk_level_enum"), nullable=False),
        sa.Column("risk_reasons", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("snapshot_path", sa.Text(), nullable=True),
        sa.Column("video_clip_path", sa.Text(), nullable=True),
        sa.Column("gps_lat", sa.Numeric(10, 8), nullable=True),
        sa.Column("gps_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("zone_name", sa.String(100), nullable=True),
        sa.Column("zone_type", sa.String(30), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("acknowledged_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("false_alarm", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("alert_id", name="pk_alerts"),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.camera_id"], name="fk_alerts_camera_id_cameras", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["acknowledged_by"], ["users.user_id"], name="fk_alerts_acknowledged_by_users", ondelete="SET NULL"
        ),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100", name=op.f("ck_alerts_risk_score_range")),
        sa.CheckConstraint("gps_lat IS NULL OR (gps_lat >= -90 AND gps_lat <= 90)", name=op.f("ck_alerts_gps_lat_range")),
        sa.CheckConstraint("gps_lng IS NULL OR (gps_lng >= -180 AND gps_lng <= 180)", name=op.f("ck_alerts_gps_lng_range")),
        sa.CheckConstraint(
            "acknowledged = false OR acknowledged_at IS NOT NULL",
            name=op.f("ck_alerts_acknowledged_requires_timestamp"),
        ),
    )
    op.create_index("idx_alerts_camera_id", "alerts", ["camera_id"])
    op.create_index("idx_alerts_track_id", "alerts", ["track_id"])
    op.create_index("idx_alerts_type", "alerts", ["alert_type"])
    op.create_index("idx_alerts_risk_level", "alerts", ["risk_level"])
    op.create_index("idx_alerts_risk_score", "alerts", ["risk_score"])
    op.create_index("idx_alerts_timestamp", "alerts", ["timestamp"])
    op.create_index("idx_alerts_acknowledged", "alerts", ["acknowledged"])
    op.create_index("idx_alerts_ack_level", "alerts", ["acknowledged", "risk_level"])
    op.create_index("idx_alerts_camera_time", "alerts", ["camera_id", "timestamp"])

    # ---- 6. events ----
    op.create_table(
        "events",
        sa.Column("event_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("person_uuid", sa.String(64), nullable=True),
        sa.Column("camera_id", sa.String(20), nullable=False),
        sa.Column("object_class", sa.String(20), nullable=False, server_default=sa.text("'person'")),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_time_seconds", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("positions", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("alert_count", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("max_risk_score", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("zone_history", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=TRUE),
        sa.PrimaryKeyConstraint("event_id", name="pk_events"),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.camera_id"], name="fk_events_camera_id_cameras", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("last_seen >= first_seen", name=op.f("ck_events_seen_order")),
        sa.CheckConstraint("total_time_seconds >= 0", name=op.f("ck_events_total_time_non_negative")),
        sa.CheckConstraint("alert_count >= 0", name=op.f("ck_events_alert_count_non_negative")),
        sa.CheckConstraint("max_risk_score >= 0 AND max_risk_score <= 100", name=op.f("ck_events_max_risk_score_range")),
    )
    op.create_index("idx_events_track_id", "events", ["track_id"])
    op.create_index("idx_events_camera_id", "events", ["camera_id"])
    op.create_index("idx_events_first_seen", "events", ["first_seen"])
    op.create_index("idx_events_active", "events", ["is_active"])
    op.create_index("idx_events_track_camera", "events", ["track_id", "camera_id"])

    # ---- 7. tracked_objects ----
    op.create_table(
        "tracked_objects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=False),
        sa.Column("camera_id", sa.String(20), nullable=False),
        sa.Column("object_class", sa.String(20), nullable=False),
        sa.Column("confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("bbox_x1", sa.Integer(), nullable=True),
        sa.Column("bbox_y1", sa.Integer(), nullable=True),
        sa.Column("bbox_x2", sa.Integer(), nullable=True),
        sa.Column("bbox_y2", sa.Integer(), nullable=True),
        sa.Column("cx", sa.Integer(), nullable=True),
        sa.Column("cy", sa.Integer(), nullable=True),
        sa.Column("in_fence", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("zone_name", sa.String(100), nullable=True),
        sa.Column("zone_type", sa.String(30), nullable=True),
        sa.Column("loitering", sa.Boolean(), nullable=False, server_default=FALSE),
        sa.Column("time_in_zone_seconds", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("direction", sa.String(20), nullable=False, server_default=sa.text("'stationary'")),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id", name="pk_tracked_objects"),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.camera_id"], name="fk_tracked_objects_camera_id_cameras", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name=op.f("ck_tracked_objects_confidence_range")
        ),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 100", name=op.f("ck_tracked_objects_risk_score_range")),
        sa.CheckConstraint("time_in_zone_seconds >= 0", name=op.f("ck_tracked_objects_time_in_zone_non_negative")),
        sa.CheckConstraint(
            "bbox_x1 IS NULL OR bbox_x2 IS NULL OR bbox_x2 >= bbox_x1", name=op.f("ck_tracked_objects_bbox_x_order")
        ),
        sa.CheckConstraint(
            "bbox_y1 IS NULL OR bbox_y2 IS NULL OR bbox_y2 >= bbox_y1", name=op.f("ck_tracked_objects_bbox_y_order")
        ),
    )
    op.create_index("idx_tracked_track_id", "tracked_objects", ["track_id"])
    op.create_index("idx_tracked_camera_id", "tracked_objects", ["camera_id"])
    op.create_index("idx_tracked_timestamp", "tracked_objects", ["timestamp"])
    op.create_index("idx_tracked_in_fence", "tracked_objects", ["in_fence"])
    op.create_index("idx_tracked_loitering", "tracked_objects", ["loitering"])
    op.create_index("idx_tracked_camera_time", "tracked_objects", ["camera_id", "timestamp"])

    # ---- 8. evidence ----
    op.create_table(
        "evidence",
        sa.Column("evidence_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alert_id", sa.String(40), nullable=True),
        sa.Column("camera_id", sa.String(20), nullable=False),
        sa.Column("track_id", sa.Integer(), nullable=True),
        sa.Column("evidence_type", enum("evidence_type_enum"), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("gps_lat", sa.Numeric(10, 8), nullable=True),
        sa.Column("gps_lng", sa.Numeric(11, 8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("is_hot_storage", sa.Boolean(), nullable=False, server_default=TRUE),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
        sa.ForeignKeyConstraint(
            ["alert_id"], ["alerts.alert_id"], name="fk_evidence_alert_id_alerts", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["camera_id"], ["cameras.camera_id"], name="fk_evidence_camera_id_cameras", ondelete="RESTRICT"
        ),
        sa.CheckConstraint("file_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_evidence_file_hash_sha256")),
        sa.CheckConstraint("file_size_bytes IS NULL OR file_size_bytes >= 0", name=op.f("ck_evidence_file_size_non_negative")),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name=op.f("ck_evidence_duration_non_negative")
        ),
        sa.CheckConstraint("gps_lat IS NULL OR (gps_lat >= -90 AND gps_lat <= 90)", name=op.f("ck_evidence_gps_lat_range")),
        sa.CheckConstraint(
            "gps_lng IS NULL OR (gps_lng >= -180 AND gps_lng <= 180)", name=op.f("ck_evidence_gps_lng_range")
        ),
        sa.CheckConstraint(
            "is_hot_storage = true OR archived_at IS NOT NULL", name=op.f("ck_evidence_archived_requires_timestamp")
        ),
    )
    op.create_index("idx_evidence_alert_id", "evidence", ["alert_id"])
    op.create_index("idx_evidence_camera_id", "evidence", ["camera_id"])
    op.create_index("idx_evidence_type", "evidence", ["evidence_type"])
    op.create_index("idx_evidence_created_at", "evidence", ["created_at"])
    op.create_index("idx_evidence_hot_storage", "evidence", ["is_hot_storage"])

    # ---- 9. hardware_registry ----
    op.create_table(
        "hardware_registry",
        sa.Column("hardware_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hardware_type", enum("hardware_type_enum"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("model_number", sa.String(100), nullable=True),
        sa.Column("manufacturer", sa.String(100), nullable=True),
        sa.Column("connection_config", postgresql.JSONB(), nullable=False, server_default=EMPTY_OBJECT),
        sa.Column("status", enum("hardware_status_enum"), nullable=False, server_default=sa.text("'standby'")),
        sa.Column("camera_id", sa.String(20), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(), nullable=False, server_default=EMPTY_ARRAY),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=True),
        sa.Column("firmware_version", sa.String(50), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("hardware_id", name="pk_hardware_registry"),
        sa.ForeignKeyConstraint(
            ["camera_id"],
            ["cameras.camera_id"],
            name="fk_hardware_registry_camera_id_cameras",
            ondelete="SET NULL",
        ),
    )
    op.create_index("idx_hardware_type", "hardware_registry", ["hardware_type"])
    op.create_index("idx_hardware_status", "hardware_registry", ["status"])
    op.create_index("idx_hardware_camera_id", "hardware_registry", ["camera_id"])

    # ---- 10. audit_logs ----
    op.create_table(
        "audit_logs",
        sa.Column("log_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("username", sa.String(50), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("table_name", sa.String(50), nullable=True),
        sa.Column("record_id", sa.String(50), nullable=True),
        sa.Column("old_value", postgresql.JSONB(), nullable=True),
        sa.Column("new_value", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(255), nullable=True),
        sa.Column("session_id", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'success'")),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("log_id", name="pk_audit_logs"),
    )
    op.create_index("idx_audit_user_id", "audit_logs", ["user_id"])
    op.create_index("idx_audit_action", "audit_logs", ["action"])
    op.create_index("idx_audit_timestamp", "audit_logs", ["timestamp"])
    op.create_index("idx_audit_status", "audit_logs", ["status"])
    op.create_index("idx_audit_user_time", "audit_logs", ["user_id", "timestamp"])

    # ---- 11. system_health ----
    op.create_table(
        "system_health",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("server_name", sa.String(50), nullable=False, server_default=sa.text("'local'")),
        sa.Column("region", sa.String(20), nullable=True),
        sa.Column("cpu_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("ram_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("ram_used_gb", sa.Numeric(6, 2), nullable=True),
        sa.Column("ram_total_gb", sa.Numeric(6, 2), nullable=True),
        sa.Column("gpu_percent", sa.Numeric(5, 2), nullable=True),
        sa.Column("gpu_memory_used_mb", sa.Integer(), nullable=True),
        sa.Column("gpu_memory_total_mb", sa.Integer(), nullable=True),
        sa.Column("disk_used_gb", sa.Numeric(8, 2), nullable=True),
        sa.Column("disk_free_gb", sa.Numeric(8, 2), nullable=True),
        sa.Column("avg_fps", sa.Numeric(5, 2), nullable=False, server_default=ZERO),
        sa.Column("cameras_online", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("cameras_total", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("active_alerts", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("critical_alerts", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("persons_detected", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("vehicles_detected", sa.Integer(), nullable=False, server_default=ZERO),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.PrimaryKeyConstraint("id", name="pk_system_health"),
        sa.CheckConstraint(
            "cpu_percent IS NULL OR (cpu_percent >= 0 AND cpu_percent <= 100)", name=op.f("ck_system_health_cpu_percent_range")
        ),
        sa.CheckConstraint(
            "ram_percent IS NULL OR (ram_percent >= 0 AND ram_percent <= 100)", name=op.f("ck_system_health_ram_percent_range")
        ),
        sa.CheckConstraint(
            "gpu_percent IS NULL OR (gpu_percent >= 0 AND gpu_percent <= 100)", name=op.f("ck_system_health_gpu_percent_range")
        ),
        sa.CheckConstraint(
            "cameras_online >= 0 AND cameras_online <= cameras_total", name=op.f("ck_system_health_cameras_online_range")
        ),
        sa.CheckConstraint(
            "critical_alerts >= 0 AND critical_alerts <= active_alerts", name=op.f("ck_system_health_critical_alerts_range")
        ),
    )
    op.create_index("idx_health_server", "system_health", ["server_name"])
    op.create_index("idx_health_timestamp", "system_health", ["timestamp"])
    op.create_index("idx_health_server_time", "system_health", ["server_name", "timestamp"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        op.drop_table(table)  # drops the table's indexes and constraints with it
    for name, values in reversed(list(ENUMS.items())):
        postgresql.ENUM(*values, name=name).drop(bind, checkfirst=True)
