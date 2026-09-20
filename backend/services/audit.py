"""Audit trail writer used by every endpoint, the WebSocket and background services."""
import logging
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.database import get_db_context
from backend.models import AuditLog, User

logger = logging.getLogger("sih26187.audit")


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    from backend.core.dependencies import get_client_ip

    return get_client_ip(request)


async def audit_action(
    db: AsyncSession,
    action: str,
    request: Optional[Request] = None,
    user: Optional[User] = None,
    table_name: Optional[str] = None,
    record_id: Any = None,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
    status: str = "success",
    commit: bool = False,
    username: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AuditLog:
    """Add an audit row to the session. Secrets in old/new values are masked by AuditLog.record.

    commit=True is used on failure paths, where the request transaction would otherwise roll back."""
    if request is not None:
        user_agent = user_agent or request.headers.get("user-agent")
        session_id = session_id or getattr(request.state, "token_jti", None)
    entry = AuditLog.record(
        action=action,
        user_id=getattr(user, "user_id", None),
        username=getattr(user, "username", None) or (username[:50] if username else None),
        table_name=table_name,
        record_id=record_id,
        old_value=old_value,
        new_value=new_value,
        ip_address=ip_address or _client_ip(request),
        user_agent=user_agent,
        session_id=session_id,
        status=status,
    )
    db.add(entry)
    if commit:
        await db.commit()
    else:
        await db.flush()
    logger.info("audit action=%s user=%s status=%s record=%s", action, entry.username, status, record_id)
    return entry


async def audit_standalone(action: str, **kwargs) -> None:
    """Write an audit row in its own transaction (for code paths without a request session)."""
    try:
        async with get_db_context() as db:
            await audit_action(db, action, **kwargs)
    except Exception:  # auditing must never crash the caller, but the failure must be visible
        logger.exception("Failed to write audit log for action %s", action)
