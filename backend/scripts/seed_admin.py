import asyncio
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.core.config import settings  # noqa: E402
from backend.core.enums import UserRole, ZoneRegion  # noqa: E402
from backend.core.security import pwd_context, validate_password_strength  # noqa: E402
from backend.database.database import AsyncSessionLocal, engine  # noqa: E402
from backend.models.audit_log import AuditLog  # noqa: E402
from backend.models.user import User  # noqa: E402


async def seed_admin() -> None:
    username = settings.ADMIN_USERNAME.lower()
    validate_password_strength(settings.ADMIN_PASSWORD)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.username == username))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Admin '{username}' already exists (user_id={existing.user_id})")
            return

        admin = User(
            user_id=uuid.uuid4(),
            username=username,
            password_hash=pwd_context.hash(settings.ADMIN_PASSWORD),
            role=UserRole.ADMIN,
            camera_access=[],
            zone_access=[region.value for region in ZoneRegion],
            is_active=True,
            failed_login_attempts=0,
        )
        db.add(admin)
        db.add(
            AuditLog.record(
                action="seed_admin",
                user_id=admin.user_id,
                username=username,
                table_name="users",
                record_id=str(admin.user_id),
                new_value={"username": username, "role": UserRole.ADMIN.value},
            )
        )
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            raise

        if not pwd_context.verify(settings.ADMIN_PASSWORD, admin.password_hash):
            raise SystemExit("Password hash verification failed after seeding")
        print(f"Admin created: {username} (user_id={admin.user_id})")
        print(f"Password: {settings.ADMIN_PASSWORD[:4]}****")
        print(f"Stored as bcrypt hash: {admin.password_hash[:7]}... (plain text never stored)")


async def main() -> None:
    try:
        await seed_admin()
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
