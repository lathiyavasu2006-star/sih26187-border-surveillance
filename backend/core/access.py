"""Row-level visibility filters shared by list endpoints."""
from typing import Optional

from sqlalchemy import or_, select

from backend.core.enums import UserRole
from backend.models import Camera, User


def visible_camera_ids_subquery(user: User):
    """SELECT camera_id of cameras the user may see, or None for admin (no restriction)."""
    if user.role == UserRole.ADMIN:
        return None
    conditions = []
    if user.camera_access:
        conditions.append(Camera.camera_id.in_(list(user.camera_access)))
    if user.zone_access:
        conditions.append(Camera.zone_region.in_(list(user.zone_access)))
    query = select(Camera.camera_id)
    if conditions:
        return query.where(or_(*conditions))
    return query.where(Camera.camera_id.is_(None))  # no grants -> sees nothing


def restrict_to_visible_cameras(query, column, user: User):
    """Apply camera visibility to any query that has a camera_id column."""
    subquery = visible_camera_ids_subquery(user)
    if subquery is None:
        return query
    return query.where(column.in_(subquery))


def normalize_camera_id(camera_id: Optional[str]) -> Optional[str]:
    return camera_id.strip().upper() if camera_id else camera_id
