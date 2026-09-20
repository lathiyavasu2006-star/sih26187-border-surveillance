"""Authentication, JWT contents, RBAC and rate limiting."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.core.auth import create_access_token, create_refresh_token, token_claims_for, verify_token
from backend.core.enums import UserRole
from backend.core.rate_limit import limiter
from backend.models import AuditLog, User

from tests.api.conftest import TEST_PASSWORD, auth_header, make_user


async def login(client, username="admin", password=TEST_PASSWORD):
    return await client.post("/auth/login", data={"username": username, "password": password})


async def test_ping_and_root_need_no_auth(client):
    ping = await client.get("/ping")
    assert ping.status_code == 200
    assert ping.json()["db"] == "connected"

    root = await client.get("/")
    assert root.status_code == 200
    assert root.json()["name"] == "SIH26187 Border Surveillance API"

    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] in {"healthy", "degraded"}


async def test_login_returns_tokens_and_user(client, admin, session):
    response = await login(client)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert body["user"]["username"] == "admin"
    assert "password_hash" not in response.text

    claims = verify_token(body["access_token"])
    assert claims["sub"] == str(admin.user_id)
    assert claims["jti"] and claims["type"] == "access"
    assert claims["iat"] and claims["exp"] > claims["iat"]

    session.expire_all()   # the endpoint committed in its own session
    refreshed = await session.execute(select(User).where(User.username == "admin"))
    assert refreshed.scalar_one().last_login is not None

    logs = (await session.execute(select(AuditLog).where(AuditLog.action == "LOGIN_SUCCESS"))).scalars().all()
    assert len(logs) == 1 and logs[0].session_id == claims["jti"]


async def test_login_wrong_password_locks_after_three_attempts(client, admin, session):
    for attempt in range(2):
        response = await login(client, password="WrongPassword#1")
        assert response.status_code == 401
        assert "locked" not in response.json()["detail"].lower()

    third = await login(client, password="WrongPassword#1")
    assert third.status_code == 401
    assert "locked for 30 minutes" in third.json()["detail"]

    # Correct password is now refused with 423 until the lock expires.
    blocked = await login(client)
    assert blocked.status_code == 423
    assert "Account locked until" in blocked.json()["detail"]

    session.expire_all()
    user = (await session.execute(select(User).where(User.username == "admin"))).scalar_one()
    assert user.failed_login_attempts == 3 and user.is_locked()

    failures = (await session.execute(select(AuditLog).where(AuditLog.action == "LOGIN_FAILED"))).scalars().all()
    assert len(failures) == 3 and all(log.status == "failure" for log in failures)
    blocked_logs = (await session.execute(select(AuditLog).where(AuditLog.action == "LOGIN_BLOCKED_LOCKED"))).scalars().all()
    assert len(blocked_logs) == 1


async def test_login_unknown_user_is_audited_and_generic(client, session):
    response = await login(client, username="ghost")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"
    logs = (await session.execute(select(AuditLog).where(AuditLog.action == "LOGIN_FAILED"))).scalars().all()
    assert len(logs) == 1 and logs[0].username == "ghost"


async def test_inactive_account_cannot_log_in(client, session):
    await make_user(session, "retired.op", UserRole.OPERATOR, is_active=False)
    response = await login(client, username="retired.op")
    assert response.status_code == 403
    assert response.json()["detail"] == "Account is disabled"


async def test_refresh_token_flow(client, admin):
    tokens = (await login(client)).json()
    response = await client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert response.status_code == 200
    assert verify_token(response.json()["access_token"])["sub"] == str(admin.user_id)

    # An access token is not a refresh token.
    wrong_type = await client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]})
    assert wrong_type.status_code == 401
    assert wrong_type.json()["detail"] == "Wrong token type"


async def test_me_logout_and_password_change(client, admin, session):
    headers = auth_header(admin)

    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert "password_hash" not in me.text

    weak = await client.post("/auth/change-password", headers=headers,
                             json={"current_password": TEST_PASSWORD, "new_password": "short"})
    assert weak.status_code == 422

    wrong = await client.post("/auth/change-password", headers=headers,
                              json={"current_password": "NotMyPassword#9", "new_password": "Fresh#Password2026"})
    assert wrong.status_code == 400

    changed = await client.post("/auth/change-password", headers=headers,
                                json={"current_password": TEST_PASSWORD, "new_password": "Fresh#Password2026"})
    assert changed.status_code == 200, changed.text
    assert (await login(client, password="Fresh#Password2026")).status_code == 200
    assert (await login(client, password=TEST_PASSWORD)).status_code == 401

    logout = await client.post("/auth/logout", headers=auth_header(admin), json={})
    assert logout.status_code == 200
    actions = {log.action for log in (await session.execute(select(AuditLog))).scalars().all()}
    assert {"VIEW_PROFILE", "CHANGE_PASSWORD", "CHANGE_PASSWORD_FAILED", "LOGOUT"} <= actions


async def test_expired_and_tampered_tokens_rejected(client, admin):
    expired = create_access_token(token_claims_for(admin), expires_delta=timedelta(seconds=-10))
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})).status_code == 401

    valid = create_access_token(token_claims_for(admin))
    tampered = valid[:-3] + ("aaa" if not valid.endswith("aaa") else "bbb")
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {tampered}"})).status_code == 401

    assert (await client.get("/auth/me")).status_code == 401
    refresh = create_refresh_token(token_claims_for(admin))
    assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {refresh}"})).status_code == 401


async def test_locked_or_disabled_user_token_is_rejected(client, session):
    user = await make_user(session, "temp.sup", UserRole.SUPERVISOR)
    headers = auth_header(user)
    assert (await client.get("/auth/me", headers=headers)).status_code == 200

    user.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
    await session.commit()
    assert (await client.get("/auth/me", headers=headers)).status_code == 423

    user.locked_until = None
    user.is_active = False
    await session.commit()
    assert (await client.get("/auth/me", headers=headers)).status_code == 403


async def test_rbac_matrix(client, session, operator_headers, supervisor_headers, admin_headers):
    # Operator cannot register cameras or hardware; supervisor cannot delete; admin can.
    camera_payload = {"name": "X", "zone_region": "north"}
    assert (await client.post("/cameras/register", headers=operator_headers, json=camera_payload)).status_code == 403
    assert (await client.post("/hardware/register", headers=supervisor_headers,
                              json={"hardware_type": "radar", "name": "R1"})).status_code == 403
    assert (await client.get("/stats/audit-log", headers=supervisor_headers)).status_code == 403
    assert (await client.get("/stats/audit-log", headers=admin_headers)).status_code == 200

    denials = (await session.execute(select(AuditLog).where(AuditLog.action == "ACCESS_DENIED"))).scalars().all()
    assert len(denials) >= 3
    assert all(log.status == "denied" for log in denials)


async def test_supervisor_cannot_register_outside_granted_region(client, supervisor_headers, fake_probe):
    fake_probe(connected=True)
    response = await client.post("/cameras/register", headers=supervisor_headers,
                                 json={"name": "South Post", "zone_region": "south"})
    assert response.status_code == 403
    assert "No access to region south" in response.json()["detail"]


async def test_login_rate_limit(client, admin):
    limiter.enabled = True
    limiter.reset()
    try:
        codes = [(await login(client, password="WrongPassword#1")).status_code for _ in range(12)]
    finally:
        limiter.enabled = False
        limiter.reset()
    assert 429 in codes, f"expected a 429 among {codes}"
    assert codes.index(429) >= 10, "the limit should allow the configured 10 requests per minute first"


async def test_cors_allows_only_configured_origins(client, admin_headers):
    allowed = await client.get("/ping", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"

    evil = await client.get("/ping", headers={"Origin": "http://evil.example.com"})
    assert "access-control-allow-origin" not in evil.headers


async def test_security_headers_present(client):
    response = await client.get("/ping")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
