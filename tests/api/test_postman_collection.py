"""The Postman collection must stay in step with the application's routes."""
import json
import re
from pathlib import Path

import pytest

from tests.conftest import PROJECT_ROOT

COLLECTION_PATH = PROJECT_ROOT / "postman_collection.json"


@pytest.fixture(scope="module")
def collection() -> dict:
    return json.loads(COLLECTION_PATH.read_text(encoding="utf-8"))


def iter_requests(items):
    for item in items:
        if "item" in item:
            yield from iter_requests(item["item"])
        elif "request" in item:
            yield item


def normalise(raw_url: str) -> str:
    path = raw_url.replace("{{base_url}}", "").split("?")[0]
    path = re.sub(r"\{\{[a-z_]+\}\}", "{param}", path)
    return path or "/"


def test_collection_is_valid_v21(collection):
    assert collection["info"]["schema"].endswith("v2.1.0/collection.json")
    assert collection["info"]["name"] == "SIH26187 Border Surveillance API"
    variables = {v["key"] for v in collection["variable"]}
    assert {"base_url", "token", "refresh_token"} <= variables
    assert next(v for v in collection["variable"] if v["key"] == "base_url")["value"] == "http://localhost:8000"
    assert next(v for v in collection["variable"] if v["key"] == "token")["value"] == ""


def test_login_saves_tokens(collection):
    login = next(item for item in iter_requests(collection["item"]) if item["name"] == "Login")
    script = "\n".join(login["event"][0]["script"]["exec"])
    assert 'pm.environment.set("token"' in script
    assert 'pm.environment.set("refresh_token"' in script
    assert login["request"]["body"]["mode"] == "urlencoded"
    assert login["request"]["auth"]["type"] == "noauth"


def test_authenticated_requests_carry_the_bearer_header(collection):
    public = {"/", "/ping", "/health", "/docs", "/auth/login", "/auth/refresh"}
    for item in iter_requests(collection["item"]):
        path = normalise(item["request"]["url"]["raw"])
        headers = {h["key"].lower(): h["value"] for h in item["request"].get("header", [])}
        if path in public:
            continue
        assert headers.get("authorization") == "Bearer {{token}}", f"{item['name']} is missing the auth header"


def test_every_api_route_is_covered(collection):
    from backend.main import app

    documented = set()
    for item in iter_requests(collection["item"]):
        documented.add((item["request"]["method"], normalise(item["request"]["url"]["raw"])))

    spec = app.openapi()
    missing = []
    for path, operations in spec["paths"].items():
        normalised = re.sub(r"\{[a-z_]+\}", "{param}", path)
        for method in operations:
            if (method.upper(), normalised) not in documented:
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"endpoints missing from the Postman collection: {missing}"


def test_collection_documents_the_websocket_contract(collection):
    websocket = next(folder for folder in collection["item"] if folder["name"] == "WebSocket")
    description = websocket["description"]
    assert "ws://localhost:8000/ws/{camera_id}?token=" in description
    for message_type in ("ping", "frame_data", "acknowledge", "frame_update", "critical_alert"):
        assert message_type in description


def test_collection_contains_no_real_secrets(collection):
    raw = COLLECTION_PATH.read_text(encoding="utf-8")
    from backend.core.config import settings

    from tests.conftest import PG_PASSWORD

    assert settings.SECRET_KEY not in raw
    assert "postgresql://" not in raw
    assert PG_PASSWORD not in raw
    assert settings.ADMIN_PASSWORD not in raw
