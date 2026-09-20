"""V002 map-drawn zones: camera ground-plane calibration and geo polygons on zones.

cameras.calibration holds the map↔image point pairs and the homography fitted from them
(backend/services/geo_calibration.py). zones.geo_polygon holds the polygon as the operator drew it on the
map; zones.polygon keeps the pixel polygon the ML fence enforces, projected from it.

Revision ID: V002
Revises: V001
Create Date: 2026-09-20 14:00:00+05:30
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "V002"
down_revision: Union[str, None] = "V001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cameras", sa.Column("calibration", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_check_constraint(
        op.f("ck_cameras_calibration_is_object"),
        "cameras",
        "calibration IS NULL OR jsonb_typeof(calibration) = 'object'",
    )

    op.add_column("zones", sa.Column("geo_polygon", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_check_constraint(
        op.f("ck_zones_geo_polygon_is_array"),
        "zones",
        "geo_polygon IS NULL OR jsonb_typeof(geo_polygon) = 'array'",
    )
    op.create_index("idx_zones_geo", "zones", ["camera_id"], postgresql_where=sa.text("geo_polygon IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("idx_zones_geo", table_name="zones")
    op.drop_constraint(op.f("ck_zones_geo_polygon_is_array"), "zones", type_="check")
    op.drop_column("zones", "geo_polygon")
    op.drop_constraint(op.f("ck_cameras_calibration_is_object"), "cameras", type_="check")
    op.drop_column("cameras", "calibration")
