"""Authentication: login with lockout, token refresh, profile, logout, password change."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.api_config import api_settings
from backend.core.auth import (
    REFRESH_TOKEN_TYPE,
    create_access_token,
    create_refresh_token,
    get_current_user,
    resolve_user_from_token,
    token_blacklist,
    token_claims_for,
    verify_token,
)
from backend.core.config import settings
from backend.core.rate_limit import limiter
from backend.core.security import verify_password
from backend.database.crud import AuthStatus, user_crud
from backend.database.database import get_db
from backend.models import User
from backend.schemas.api import (
    AccessTokenResponse,
    LoginUserInfo,
    LogoutRequest,
    MessageResponse,
    RefreshRequest,
    TokenResponse,
)
from backend.schemas.user import UserPasswordChange, UserResponse
from backend.services.audit import audit_action

router = APIRouter(tags=["Auth"])

INVALID_CREDENTIALS = "Invalid username or password"


def _access_expires_in() -> int:
    return settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


@router.post("/login", response_model=TokenResponse, summary="Login (OAuth2 password form)")
@limiter.limit(api_settings.AUTH_LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    username = (form.username or "").strip().lower()
    if not username or len(username) > 50:
        await audit_action(db, "LOGIN_FAILED", request=request, username=username[:50] or None, table_name="users",
                           new_value={"reason": "malformed_username"}, status="failure", commit=True)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_CREDENTIALS, headers={"WWW-Authenticate": "Bearer"})

    existing = await user_crud.get_by_username(db, username)
    if existing is not None and existing.is_locked():
        await audit_action(db, "LOGIN_BLOCKED_LOCKED", request=request, user=existing, table_name="users",
                           record_id=existing.user_id,
                           new_value={"locked_until": existing.locked_until.isoformat()}, status="denied", commit=True)
        raise HTTPException(status.HTTP_423_LOCKED, f"Account locked until {existing.locked_until.isoformat()}")

    result = await user_crud.authenticate(db, username, form.password)

    if result.status in (AuthStatus.INVALID_CREDENTIALS, AuthStatus.LOCKED):
        just_locked = result.status == AuthStatus.LOCKED
        await audit_action(
            db, "LOGIN_FAILED", request=request, user=existing, username=username, table_name="users",
            record_id=getattr(existing, "user_id", None),
            new_value={
                "reason": "unknown_user" if existing is None else "wrong_password",
                "failed_login_attempts": getattr(existing, "failed_login_attempts", None),
                "account_locked": just_locked,
            },
            status="failure", commit=True,
        )
        detail = INVALID_CREDENTIALS
        if just_locked:
            detail += f". Account locked for {settings.ACCOUNT_LOCKOUT_MINUTES} minutes after {settings.MAX_FAILED_LOGIN_ATTEMPTS} failed attempts"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})

    if result.status == AuthStatus.INACTIVE:
        await audit_action(db, "LOGIN_DENIED_INACTIVE", request=request, user=existing, table_name="users",
                           record_id=existing.user_id, status="denied", commit=True)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is disabled")

    user = result.user
    claims = token_claims_for(user)
    access_token = create_access_token(claims)
    refresh_token = create_refresh_token(claims)
    session_jti = verify_token(access_token)["jti"]
    await audit_action(db, "LOGIN_SUCCESS", request=request, user=user, table_name="users", record_id=user.user_id,
                       new_value={"last_login": user.last_login.isoformat()}, session_id=session_jti)
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=_access_expires_in(),
        user=LoginUserInfo.model_validate(user),
    )


@router.post("/refresh", response_model=AccessTokenResponse, summary="Exchange a refresh token for a new access token")
@limiter.limit(api_settings.AUTH_REFRESH_RATE_LIMIT)
async def refresh(
    request: Request,
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
) -> AccessTokenResponse:
    try:
        user, claims = await resolve_user_from_token(db, payload.refresh_token, expected_type=REFRESH_TOKEN_TYPE)
    except HTTPException as exc:
        await audit_action(db, "TOKEN_REFRESH_FAILED", request=request, table_name="users",
                           new_value={"reason": exc.detail}, status="failure", commit=True)
        raise
    access_token = create_access_token(token_claims_for(user))
    await audit_action(db, "TOKEN_REFRESH", request=request, user=user, table_name="users", record_id=user.user_id,
                       session_id=claims["jti"])
    await db.commit()
    return AccessTokenResponse(access_token=access_token, expires_in=_access_expires_in())


@router.get("/me", response_model=UserResponse, summary="Current user profile")
@limiter.limit(api_settings.AUTH_SENSITIVE_RATE_LIMIT)
async def me(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    await audit_action(db, "VIEW_PROFILE", request=request, user=current_user, table_name="users",
                       record_id=current_user.user_id)
    await db.commit()
    return UserResponse.model_validate(current_user)


@router.post("/logout", response_model=MessageResponse, summary="Logout (revokes tokens when blacklist is enabled)")
@limiter.limit(api_settings.AUTH_SENSITIVE_RATE_LIMIT)
async def logout(
    request: Request,
    payload: Optional[LogoutRequest] = Body(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    token_blacklist.revoke(request.state.token_jti, float(request.state.token_exp))
    refresh_revoked = False
    if payload and payload.refresh_token:
        try:
            refresh_claims = verify_token(payload.refresh_token, expected_type=REFRESH_TOKEN_TYPE)
            if refresh_claims["sub"] == str(current_user.user_id):
                token_blacklist.revoke(refresh_claims["jti"], float(refresh_claims["exp"]))
                refresh_revoked = token_blacklist.enabled
        except HTTPException:
            pass
    await audit_action(db, "LOGOUT", request=request, user=current_user, table_name="users",
                       record_id=current_user.user_id,
                       new_value={"blacklist_enabled": token_blacklist.enabled, "refresh_token_revoked": refresh_revoked})
    await db.commit()
    return MessageResponse(message="Logged out successfully")


@router.post("/change-password", response_model=MessageResponse, summary="Change own password")
@limiter.limit(api_settings.AUTH_SENSITIVE_RATE_LIMIT)
async def change_password(
    request: Request,
    payload: UserPasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if not verify_password(payload.current_password, current_user.password_hash):
        locked = current_user.register_failed_login(datetime.now(timezone.utc))
        await audit_action(db, "CHANGE_PASSWORD_FAILED", request=request, user=current_user, table_name="users",
                           record_id=current_user.user_id,
                           new_value={"reason": "wrong_current_password", "account_locked": locked},
                           status="failure", commit=True)
        if locked:
            raise HTTPException(status.HTTP_423_LOCKED, f"Account locked until {current_user.locked_until.isoformat()}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")

    if current_user.username.lower() in payload.new_password.lower():
        await audit_action(db, "CHANGE_PASSWORD_FAILED", request=request, user=current_user, table_name="users",
                           record_id=current_user.user_id, new_value={"reason": "password_contains_username"},
                           status="failure", commit=True)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Password must not contain the username")

    await user_crud.set_password(db, current_user, payload.new_password)
    token_blacklist.revoke(request.state.token_jti, float(request.state.token_exp))
    await audit_action(db, "CHANGE_PASSWORD", request=request, user=current_user, table_name="users",
                       record_id=current_user.user_id, new_value={"password": "changed"})
    await db.commit()
    return MessageResponse(message="Password changed successfully")
