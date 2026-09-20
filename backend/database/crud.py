"""Concrete CRUD objects with domain operations (auth lockout, alert triage, evidence integrity)."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from backend.database.crud_base import CRUDBase, InputData, _to_dict
from backend.models import (
    Alert,
    AuditLog,
    Camera,
    Event,
    Evidence,
    HardwareRegistry,
    SystemHealth,
    TrackedObject,
    User,
    Zone,
)
from backend.schemas.alert import AlertAcknowledge, AlertFilter


class AuthStatus(str, Enum):
    SUCCESS = "success"
    INVALID_CREDENTIALS = "invalid_credentials"
    LOCKED = "locked"
    INACTIVE = "inactive"


@dataclass
class AuthResult:
    status: AuthStatus
    user: Optional[User] = None
    locked_seconds_remaining: int = 0

    @property
    def ok(self) -> bool:
        return self.status == AuthStatus.SUCCESS


class UserCRUD(CRUDBase[User]):
    async def get_by_username(self, db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.username == username.lower()))
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, obj_in: InputData) -> User:
        data = _to_dict(obj_in)
        password = data.pop("password", None)
        if not password:
            raise ValueError("password is required to create a user")
        data.pop("password_hash", None)
        data["username"] = data["username"].lower()
        data["password_hash"] = hash_password(password)
        return await super().create(db, data)

    async def update(self, db: AsyncSession, db_obj: User, obj_in: InputData, exclude_none: bool = True) -> User:
        data = _to_dict(obj_in, exclude_unset=True)
        # Passwords can only change through set_password (hashing + lock reset).
        data.pop("password", None)
        data.pop("password_hash", None)
        return await super().update(db, db_obj, data, exclude_none=exclude_none)

    async def set_password(self, db: AsyncSession, user: User, new_password: str) -> User:
        user.password_hash = hash_password(new_password)
        user.unlock()
        db.add(user)
        await db.flush()
        return user

    async def authenticate(self, db: AsyncSession, username: str, password: str) -> AuthResult:
        """Verify credentials enforcing lockout: 3 consecutive failures lock the account for 30 minutes."""
        now = datetime.now(timezone.utc)
        user = await self.get_by_username(db, username)
        if user is None:
            verify_password(password, DUMMY_PASSWORD_HASH)  # equalise response timing
            return AuthResult(AuthStatus.INVALID_CREDENTIALS)

        if user.is_locked(now):
            return AuthResult(AuthStatus.LOCKED, locked_seconds_remaining=user.lock_remaining_seconds(now))

        if not verify_password(password, user.password_hash):
            locked = user.register_failed_login(now)
            db.add(user)
            await db.flush()
            if locked:
                return AuthResult(AuthStatus.LOCKED, locked_seconds_remaining=user.lock_remaining_seconds(now))
            return AuthResult(AuthStatus.INVALID_CREDENTIALS)

        if not user.is_active:
            return AuthResult(AuthStatus.INACTIVE)

        if password_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        user.register_successful_login(now)
        db.add(user)
        await db.flush()
        return AuthResult(AuthStatus.SUCCESS, user=user)


class AlertCRUD(CRUDBase[Alert]):
    def _filtered(self, query, filters: AlertFilter):
        if filters.camera_id:
            query = query.where(Alert.camera_id == filters.camera_id.upper())
        if filters.alert_type:
            query = query.where(Alert.alert_type == filters.alert_type)
        if filters.risk_level:
            query = query.where(Alert.risk_level == filters.risk_level)
        if filters.acknowledged is not None:
            query = query.where(Alert.acknowledged == filters.acknowledged)
        if filters.date_from:
            query = query.where(Alert.timestamp >= filters.date_from)
        if filters.date_to:
            query = query.where(Alert.timestamp <= filters.date_to)
        return query

    async def search(self, db: AsyncSession, filters: AlertFilter) -> Tuple[List[Alert], int, int]:
        """Returns (items, total matching, unacknowledged matching)."""
        items_query = self._filtered(select(Alert), filters).order_by(Alert.timestamp.desc(), Alert.alert_id)
        items = (await db.execute(items_query.offset(filters.skip).limit(filters.limit))).scalars().all()
        total = (await db.execute(self._filtered(select(func.count()).select_from(Alert), filters))).scalar() or 0
        unack_query = self._filtered(select(func.count()).select_from(Alert), filters).where(
            Alert.acknowledged.is_(False)
        )
        unacknowledged = (await db.execute(unack_query)).scalar() or 0
        return list(items), int(total), int(unacknowledged)

    async def acknowledge(self, db: AsyncSession, alert_id: str, payload: AlertAcknowledge) -> Optional[Alert]:
        alert = await self.get(db, alert_id)
        if alert is None:
            return None
        if alert.acknowledged:
            raise ValueError(f"Alert {alert_id} is already acknowledged")
        alert.acknowledge(payload.acknowledged_by, payload.false_alarm, payload.notes)
        db.add(alert)
        await db.flush()
        await db.refresh(alert)
        return alert

    async def unacknowledged_count(self, db: AsyncSession, camera_id: Optional[str] = None) -> int:
        query = select(func.count()).select_from(Alert).where(Alert.acknowledged.is_(False))
        if camera_id:
            query = query.where(Alert.camera_id == camera_id)
        return int((await db.execute(query)).scalar() or 0)


class EvidenceCRUD(CRUDBase[Evidence]):
    async def verify(self, db: AsyncSession, evidence_id: int) -> Optional[bool]:
        evidence = await self.get(db, evidence_id)
        return None if evidence is None else evidence.verify_integrity()

    async def archive_older_than(self, db: AsyncSession, cutoff: datetime) -> int:
        result = await db.execute(
            select(Evidence).where(Evidence.is_hot_storage.is_(True), Evidence.created_at < cutoff)
        )
        rows = result.scalars().all()
        now = datetime.now(timezone.utc)
        for row in rows:
            row.archive(now)
        await db.flush()
        return len(rows)


class AuditLogCRUD(CRUDBase[AuditLog]):
    async def log(self, db: AsyncSession, action: str, user: Optional[User] = None, **kwargs) -> AuditLog:
        entry = AuditLog.record(
            action=action,
            user_id=getattr(user, "user_id", kwargs.pop("user_id", None)),
            username=getattr(user, "username", kwargs.pop("username", None)),
            **kwargs,
        )
        db.add(entry)
        await db.flush()
        return entry

    async def for_user(self, db: AsyncSession, user_id: UUID, limit: int = 100) -> List[AuditLog]:
        result = await db.execute(
            select(AuditLog).where(AuditLog.user_id == user_id).order_by(AuditLog.timestamp.desc()).limit(limit)
        )
        return list(result.scalars().all())


camera_crud = CRUDBase(Camera)
user_crud = UserCRUD(User)
zone_crud = CRUDBase(Zone)
alert_crud = AlertCRUD(Alert)
event_crud = CRUDBase(Event)
tracked_object_crud = CRUDBase(TrackedObject)
evidence_crud = EvidenceCRUD(Evidence)
hardware_crud = CRUDBase(HardwareRegistry)
audit_log_crud = AuditLogCRUD(AuditLog)
system_health_crud = CRUDBase(SystemHealth)
