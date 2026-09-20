"""JWT authentication and role-based access control."""
import time
import uuid
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Dict, List, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Path, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import ExpiredSignatureError, JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.api_config import api_settings
from backend.core.config import settings
from backend.core.enums import UserRole
from backend.database.database import get_db
from backend.models import Camera, User

ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"
TOKEN_ISSUER = "sih26187-border-surveillance"
TOKEN_AUDIENCE = "sih26187-api"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

CREDENTIALS_EXCEPTION_HEADERS = {"WWW-Authenticate": "Bearer"}


# --------------------------------------------------------------------------- token blacklist

class TokenBlacklist:
    """In-memory jti revocation list, active only when JWT_BLACKLIST_ENABLED=True."""

    def __init__(self) -> None:
        self._revoked: Dict[str, float] = {}
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return api_settings.JWT_BLACKLIST_ENABLED

    def revoke(self, jti: str, expires_at: float) -> None:
        if not self.enabled or not jti:
            return
        with self._lock:
            self._revoked[jti] = expires_at
            self._purge()

    def is_revoked(self, jti: Optional[str]) -> bool:
        if not self.enabled or not jti:
            return False
        with self._lock:
            self._purge()
            return jti in self._revoked

    def _purge(self) -> None:
        now = time.time()
        for key in [k for k, exp in self._revoked.items() if exp <= now]:
            del self._revoked[key]

    def clear(self) -> None:
        with self._lock:
            self._revoked.clear()


token_blacklist = TokenBlacklist()


# --------------------------------------------------------------------------- JWT helpers

def _encode(data: dict, expires_delta: timedelta, token_type: str) -> str:
    now = datetime.now(timezone.utc)
    payload = dict(data)
    if "sub" in payload:
        payload["sub"] = str(payload["sub"])
    payload.update(
        {
            "exp": now + expires_delta,
            "iat": now,
            "nbf": now,
            "jti": uuid.uuid4().hex,
            "type": token_type,
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
        }
    )
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    return _encode(
        data,
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        ACCESS_TOKEN_TYPE,
    )


def create_refresh_token(data: dict) -> str:
    return _encode(data, timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES), REFRESH_TOKEN_TYPE)


def verify_token(token: str, expected_type: str = ACCESS_TOKEN_TYPE) -> Dict[str, Any]:
    """Decode and validate a JWT. Raises HTTPException(401) for any problem."""
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers=CREDENTIALS_EXCEPTION_HEADERS)
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],  # pinned: never trust the header's alg
            audience=TOKEN_AUDIENCE,
            issuer=TOKEN_ISSUER,
            options={"require_exp": True, "require_iat": True, "require_sub": True, "require_jti": True},
        )
    except ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has expired", headers=CREDENTIALS_EXCEPTION_HEADERS)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token", headers=CREDENTIALS_EXCEPTION_HEADERS)

    if payload.get("type") != expected_type:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong token type", headers=CREDENTIALS_EXCEPTION_HEADERS)
    if token_blacklist.is_revoked(payload.get("jti")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked", headers=CREDENTIALS_EXCEPTION_HEADERS)
    try:
        UUID(payload["sub"])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject", headers=CREDENTIALS_EXCEPTION_HEADERS)
    return payload


def token_claims_for(user: User) -> dict:
    return {"sub": str(user.user_id), "username": user.username, "role": user.role.value}


async def resolve_user_from_token(db: AsyncSession, token: str, expected_type: str = ACCESS_TOKEN_TYPE) -> tuple:
    """Shared by HTTP dependencies, the WebSocket endpoint and the secured evidence file mount."""
    payload = verify_token(token, expected_type)
    user = (await db.execute(select(User).where(User.user_id == UUID(payload["sub"])))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User no longer exists", headers=CREDENTIALS_EXCEPTION_HEADERS)
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")
    if user.is_locked():
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"Account locked until {user.locked_until.isoformat()}",
        )
    return user, payload


async def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    user, payload = await resolve_user_from_token(db, token)
    request.state.user = user
    request.state.token_jti = payload["jti"]
    request.state.token_exp = payload["exp"]
    return user


# --------------------------------------------------------------------------- RBAC

async def _deny(request: Request, db: AsyncSession, user: User, action: str, detail: str, record_id: Optional[str] = None):
    """Persist an ACCESS_DENIED audit row before raising (the request transaction is otherwise rolled back)."""
    from backend.services.audit import audit_action

    await audit_action(
        db,
        action="ACCESS_DENIED",
        request=request,
        user=user,
        record_id=record_id,
        new_value={"attempted": action, "reason": detail, "path": request.url.path, "method": request.method},
        status="denied",
        commit=True,
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail)


def require_role(roles: List[str]) -> Callable:
    allowed = {getattr(role, "value", role) for role in roles}

    async def role_checker(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        if current_user.role.value not in allowed:
            await _deny(
                request,
                db,
                current_user,
                "require_role",
                f"Role '{current_user.role.value}' is not permitted; requires one of {sorted(allowed)}",
            )
        return current_user

    role_checker.__name__ = f"require_role_{'_'.join(sorted(allowed))}"
    return role_checker


def user_can_access_camera(user: User, camera_id: str, zone_region: Optional[str] = None) -> bool:
    """Admin: everything. Others: camera explicitly granted OR camera's region granted."""
    if user.role == UserRole.ADMIN:
        return True
    if camera_id in (user.camera_access or []):
        return True
    region = getattr(zone_region, "value", zone_region)
    return region is not None and region in (user.zone_access or [])


async def ensure_camera_access(request: Request, db: AsyncSession, user: User, camera_id: str) -> Camera:
    """Load the camera and enforce access. 404 if missing, 403 (audited) if not permitted."""
    camera = await db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Camera {camera_id} not found")
    if not user_can_access_camera(user, camera.camera_id, camera.zone_region):
        await _deny(request, db, user, "camera_access", f"No access to camera {camera_id}", record_id=camera_id)
    return camera


async def require_camera_access(
    request: Request,
    camera_id: str = Path(..., min_length=2, max_length=20, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{1,19}$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Camera:
    return await ensure_camera_access(request, db, current_user, camera_id.upper())


async def require_zone_access(
    request: Request,
    zone_region: str = Query(..., pattern=r"^(north|south|east|west)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    if current_user.role != UserRole.ADMIN and zone_region not in (current_user.zone_access or []):
        await _deny(request, db, current_user, "zone_access", f"No access to region {zone_region}", record_id=zone_region)
    return current_user
