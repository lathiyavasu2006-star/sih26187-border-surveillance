"""ASGI protocol compliance and global rate limiting.

httpx's ASGITransport accepts out-of-order ASGI messages that uvicorn rejects, so the Week 2 suite never saw
slowapi re-sending `http.response.start` before every body chunk. This harness enforces uvicorn's rules:
exactly one response start, then body messages, nothing after the final body.
"""
import asyncio
from typing import Dict, List, Optional, Tuple

import pytest

from backend.core.api_config import api_settings
from backend.core.rate_limit import limiter

from tests.api.conftest import auth_header


class ASGIProtocolError(AssertionError):
    pass


async def strict_call(app, method: str, path: str, headers: Optional[Dict[str, str]] = None,
                      body: bytes = b"", client: Tuple[str, int] = ("127.0.0.1", 50123)) -> Tuple[int, Dict[str, str], bytes, List[dict]]:
    messages: List[dict] = []
    finished = asyncio.Event()
    request_delivered = False

    async def receive():
        nonlocal request_delivered
        if not request_delivered:
            request_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        kind = message["type"]
        if finished.is_set():
            raise ASGIProtocolError(f"{kind} sent after the response completed")
        if not messages and kind != "http.response.start":
            raise ASGIProtocolError(f"expected http.response.start first, got {kind}")
        if messages and kind != "http.response.body":
            raise ASGIProtocolError(f"Expected ASGI message 'http.response.body', but got '{kind}'")
        messages.append(message)
        if kind == "http.response.body" and not message.get("more_body", False):
            finished.set()

    raw_headers = [(b"host", b"testserver")]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode(), value.encode()))
    if body:
        raw_headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": raw_headers, "client": client, "server": ("testserver", 80), "state": {},
    }
    await app(scope, receive, send)
    if not finished.is_set():
        raise ASGIProtocolError("response never completed")

    start = messages[0]
    response_headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    payload = b"".join(m.get("body", b"") for m in messages[1:])
    return start["status"], response_headers, payload, messages


@pytest.fixture
def rate_limiting_enabled():
    limiter.enabled = True
    limiter.reset()
    yield
    limiter.enabled = False
    limiter.reset()


@pytest.fixture
def app():
    from backend.main import app as fastapi_app

    return fastapi_app


@pytest.mark.parametrize("path", ["/", "/ping", "/health", "/docs", "/openapi.json", "/does-not-exist"])
async def test_public_routes_follow_asgi_protocol(app, rate_limiting_enabled, path):
    status, _, body, messages = await strict_call(app, "GET", path)
    assert status in (200, 404)
    assert sum(m["type"] == "http.response.start" for m in messages) == 1
    assert body


async def test_authenticated_routes_follow_asgi_protocol(app, rate_limiting_enabled, admin, camera):
    for path in ("/cameras", "/alerts", "/zones/CAM-N-001", "/stats", "/auth/me"):
        status, _, _, messages = await strict_call(app, "GET", path, headers=auth_header(admin))
        assert status == 200, path
        assert sum(m["type"] == "http.response.start" for m in messages) == 1, path


async def test_large_streamed_evidence_download_is_compliant_and_complete(app, rate_limiting_enabled, client,
                                                                         supervisor_headers, admin, camera):
    import hashlib

    content = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + bytes(range(256)) * 2048  # ~500 KB, many chunks
    uploaded = await client.post("/evidence/upload", headers=supervisor_headers,
                                 files={"file": ("clip.mp4", content, "video/mp4")}, data={"camera_id": "CAM-N-001"})
    assert uploaded.status_code == 201, uploaded.text

    status, headers, body, messages = await strict_call(app, "GET", uploaded.json()["file_url"], headers=auth_header(admin))
    assert status == 200
    assert len([m for m in messages if m["type"] == "http.response.body"]) > 1, "file must be streamed in chunks"
    assert hashlib.sha256(body).hexdigest() == uploaded.json()["file_hash"]
    assert headers["x-content-type-options"] == "nosniff"


async def test_default_limit_applies_to_router_endpoints(app, rate_limiting_enabled, admin, camera, monkeypatch):
    monkeypatch.setattr(api_settings, "RATE_LIMIT_PER_MINUTE", 5)
    headers = auth_header(admin)
    statuses = []
    for path in ("/cameras", "/alerts", "/zones/CAM-N-001", "/stats", "/cameras", "/alerts"):
        status, response_headers, body, _ = await strict_call(app, "GET", path, headers=headers)
        statuses.append(status)
    assert statuses[:5] == [200] * 5, statuses
    assert statuses[5] == 429, "the per-client budget is shared across every router endpoint"
    assert int(response_headers["retry-after"]) >= 1
    assert b"Rate limit exceeded" in body


async def test_default_limit_is_per_client(app, rate_limiting_enabled, monkeypatch):
    monkeypatch.setattr(api_settings, "RATE_LIMIT_PER_MINUTE", 2)
    first = [(await strict_call(app, "GET", "/", client=("10.0.0.1", 1)))[0] for _ in range(3)]
    other = (await strict_call(app, "GET", "/", client=("10.0.0.2", 1)))[0]
    assert first == [200, 200, 429]
    assert other == 200


async def test_preflight_and_disabled_limiter_are_not_counted(app, monkeypatch):
    monkeypatch.setattr(api_settings, "RATE_LIMIT_PER_MINUTE", 1)
    limiter.enabled = True
    limiter.reset()
    try:
        preflight_headers = {"origin": "http://localhost:5173", "access-control-request-method": "GET"}
        for _ in range(3):
            status, headers, _, _ = await strict_call(app, "OPTIONS", "/cameras", headers=preflight_headers)
            assert status == 200 and headers["access-control-allow-origin"] == "http://localhost:5173"
        assert (await strict_call(app, "GET", "/"))[0] == 200
        assert (await strict_call(app, "GET", "/"))[0] == 429
    finally:
        limiter.enabled = False
        limiter.reset()
    assert (await strict_call(app, "GET", "/"))[0] == 200, "tests and operators can disable limiting"


async def test_rate_limited_response_still_gets_cors_and_security_headers(app, rate_limiting_enabled, monkeypatch):
    monkeypatch.setattr(api_settings, "RATE_LIMIT_PER_MINUTE", 1)
    origin = {"origin": "http://localhost:3000"}
    await strict_call(app, "GET", "/", headers=origin)
    status, headers, _, _ = await strict_call(app, "GET", "/", headers=origin)
    assert status == 429
    assert headers["access-control-allow-origin"] == "http://localhost:3000", "browsers must be able to read the 429"
    assert headers["x-frame-options"] == "DENY"


async def test_oversized_upload_rejected_before_body_is_read(app, monkeypatch):
    status, _, body, messages = await strict_call(
        app, "POST", "/evidence/upload",
        headers={"content-length": str(api_settings.upload_max_bytes + 10 * 1024 * 1024)},
    )
    assert status == 413 and b"exceeds" in body
    assert sum(m["type"] == "http.response.start" for m in messages) == 1
