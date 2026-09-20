"""Common FastAPI dependencies."""
from dataclasses import dataclass
from ipaddress import ip_address

from fastapi import Depends, HTTPException, Query, Request, status

from backend.core.api_config import api_settings
from backend.core.auth import get_current_user, require_role
from backend.database.database import get_db
from backend.models import User

__all__ = [
    "get_db",
    "get_current_user",
    "get_current_active_user",
    "get_admin_user",
    "get_admin_or_regional_head",
    "get_supervisor_or_above",
    "get_operator_or_above",
    "PaginationParams",
    "get_client_ip",
]


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if current_user.is_locked():
        raise HTTPException(status.HTTP_423_LOCKED, f"Account locked until {current_user.locked_until.isoformat()}")
    return current_user


get_admin_user = require_role(["admin"])
get_admin_or_regional_head = require_role(["admin", "regional_head"])
get_supervisor_or_above = require_role(["admin", "regional_head", "supervisor"])
get_operator_or_above = require_role(["admin", "regional_head", "supervisor", "operator"])


@dataclass
class PaginationParams:
    skip: int = Query(0, ge=0, description="Rows to skip")
    limit: int = Query(100, ge=1, le=1000, description="Maximum rows to return (max 1000)")


def _valid_ip(value: str) -> bool:
    try:
        ip_address(value)
        return True
    except ValueError:
        return False


def get_client_ip(request: Request) -> str:
    """Real client IP. X-Forwarded-For is trusted only when the direct peer is a configured proxy
    (otherwise any client could spoof its address in the audit trail)."""
    peer = request.client.host if request.client else "0.0.0.0"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and peer in api_settings.trusted_proxies:
        for candidate in (part.strip() for part in forwarded.split(",")):
            if candidate and _valid_ip(candidate):
                return candidate
    real_ip = request.headers.get("x-real-ip")
    if real_ip and peer in api_settings.trusted_proxies and _valid_ip(real_ip.strip()):
        return real_ip.strip()
    return peer if _valid_ip(peer) else "0.0.0.0"
