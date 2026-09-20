"""Evidence upload, hashing, integrity verification, archival, deletion and authenticated downloads."""
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.core.config import settings
from backend.models import AuditLog, Evidence

from tests.api.conftest import auth_header, jpeg_bytes


def mp4_bytes(size: int = 2048) -> bytes:
    header = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
    return header + b"\x00" * (size - len(header))


async def upload(client, headers, content: bytes, filename="clip.mp4", camera_id="CAM-N-001", **fields):
    data = {"camera_id": camera_id, **{k: str(v) for k, v in fields.items()}}
    return await client.post(
        "/evidence/upload",
        headers=headers,
        files={"file": (filename, content, "application/octet-stream")},
        data=data,
    )


async def test_upload_computes_sha256_immediately(client, camera, supervisor_headers, session, api_environment):
    content = mp4_bytes()
    response = await upload(client, supervisor_headers, content)
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["file_hash"] == hashlib.sha256(content).hexdigest()
    assert len(body["file_hash"]) == 64
    assert body["file_size_bytes"] == len(content)
    assert body["evidence_type"] == "uploaded_video"
    assert body["job_id"] and len(body["job_id"]) == 32
    assert body["file_url"].startswith("/evidence/files/uploads/")

    evidence = (await session.execute(select(Evidence))).scalar_one()
    stored = Path(evidence.file_path)
    assert stored.is_file() and stored.read_bytes() == content
    assert stored.parent == Path(api_environment) / "uploads"

    log = (await session.execute(select(AuditLog).where(AuditLog.action == "UPLOAD_EVIDENCE"))).scalar_one()
    assert log.new_value["file_hash"] == body["file_hash"]


async def test_upload_image_is_manual_snapshot(client, camera, supervisor_headers):
    response = await upload(client, supervisor_headers, jpeg_bytes(), filename="snap.jpg")
    assert response.status_code == 201
    assert response.json()["evidence_type"] == "manual_snapshot"


async def test_upload_rejects_wrong_type_and_disguised_content(client, camera, supervisor_headers, session):
    bad_ext = await upload(client, supervisor_headers, b"MZ\x90\x00", filename="virus.exe")
    assert bad_ext.status_code == 415

    # .mp4 extension but the bytes are not a video container
    disguised = await upload(client, supervisor_headers, b"just plain text pretending", filename="fake.mp4")
    assert disguised.status_code == 415
    assert "does not match" in disguised.json()["detail"]

    assert (await session.execute(select(Evidence))).scalars().all() == []
    failures = (await session.execute(select(AuditLog).where(AuditLog.status == "failure"))).scalars().all()
    assert len(failures) >= 2


async def test_upload_rejects_path_traversal_filename(client, camera, supervisor_headers, session, api_environment):
    response = await upload(client, supervisor_headers, jpeg_bytes(), filename="../../../../windows/evil.jpg")
    assert response.status_code == 201
    evidence = (await session.execute(select(Evidence))).scalar_one()
    stored = Path(evidence.file_path)
    assert stored.parent == Path(api_environment) / "uploads"
    assert "windows" not in str(stored).lower().replace(str(api_environment).lower(), "")
    assert stored.name.endswith("evil.jpg")


async def test_upload_requires_supervisor_and_camera_access(client, camera, operator_headers, outsider):
    assert (await upload(client, operator_headers, jpeg_bytes(), filename="a.jpg")).status_code == 403
    assert (await upload(client, auth_header(outsider), jpeg_bytes(), filename="a.jpg")).status_code == 403


async def test_upload_alert_must_belong_to_camera(client, camera, admin_headers, supervisor_headers, session):
    from tests.api.test_api_alerts_events import seed_alert

    alert = await seed_alert(session)
    ok = await upload(client, supervisor_headers, jpeg_bytes(), filename="a.jpg", alert_id=alert.alert_id)
    assert ok.status_code == 201

    missing = await upload(client, supervisor_headers, jpeg_bytes(), filename="b.jpg", alert_id="ALT-DOES-NOT-EXIST")
    assert missing.status_code == 404


async def test_verify_detects_tampering_and_missing_files(client, camera, supervisor_headers, admin_headers, session):
    content = mp4_bytes()
    evidence_id = (await upload(client, supervisor_headers, content)).json()["evidence_id"]

    valid = await client.get(f"/evidence/{evidence_id}/verify", headers=admin_headers)
    assert valid.json()["integrity"] == "valid"
    assert valid.json()["computed_hash"] == valid.json()["stored_hash"]

    evidence = (await session.execute(select(Evidence))).scalar_one()
    Path(evidence.file_path).write_bytes(mp4_bytes() + b"tampered")
    tampered = await client.get(f"/evidence/{evidence_id}/verify", headers=admin_headers)
    assert tampered.json()["integrity"] == "tampered"
    assert tampered.json()["computed_hash"] != tampered.json()["stored_hash"]

    Path(evidence.file_path).unlink()
    missing = await client.get(f"/evidence/{evidence_id}/verify", headers=admin_headers)
    assert missing.json()["integrity"] == "missing"
    assert missing.json()["computed_hash"] is None

    logs = (await session.execute(select(AuditLog).where(AuditLog.action == "VERIFY_EVIDENCE"))).scalars().all()
    assert [log.status for log in logs] == ["success", "failure", "failure"]


async def test_list_and_detail(client, camera, supervisor_headers, admin_headers):
    await upload(client, supervisor_headers, mp4_bytes(), filename="one.mp4")
    await upload(client, supervisor_headers, jpeg_bytes(), filename="two.jpg")

    listing = await client.get("/evidence", headers=admin_headers)
    assert listing.json()["total"] == 2
    assert all(item["file_exists"] for item in listing.json()["items"])

    videos = await client.get("/evidence", headers=admin_headers, params={"evidence_type": "uploaded_video"})
    assert videos.json()["total"] == 1

    evidence_id = listing.json()["items"][0]["evidence_id"]
    detail = await client.get(f"/evidence/{evidence_id}", headers=admin_headers)
    assert detail.status_code == 200 and detail.json()["file_url"]


async def test_archive_moves_to_cold_storage(client, camera, supervisor_headers, admin_headers, session, api_environment):
    evidence_id = (await upload(client, supervisor_headers, mp4_bytes())).json()["evidence_id"]

    response = await client.post(f"/evidence/{evidence_id}/archive", headers=admin_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["is_hot_storage"] is False and body["archived_at"]

    archived = Path(body["file_path"])
    assert archived.is_file() and archived.parent == Path(api_environment) / "cold_storage"

    # verification still passes after the move (the hash travels with the file)
    assert (await client.get(f"/evidence/{evidence_id}/verify", headers=admin_headers)).json()["integrity"] == "valid"
    # archiving twice is a conflict
    assert (await client.post(f"/evidence/{evidence_id}/archive", headers=admin_headers)).status_code == 409


async def test_archive_refuses_tampered_file(client, camera, supervisor_headers, admin_headers, session):
    evidence_id = (await upload(client, supervisor_headers, mp4_bytes())).json()["evidence_id"]
    evidence = (await session.execute(select(Evidence))).scalar_one()
    Path(evidence.file_path).write_bytes(b"\x00\x00\x00\x18ftypmp42 tampered")

    response = await client.post(f"/evidence/{evidence_id}/archive", headers=admin_headers)
    assert response.status_code == 409
    assert "integrity check" in response.json()["detail"]
    assert Path(evidence.file_path).is_file(), "file must stay in place when archiving is refused"


async def test_delete_evidence_admin_only(client, camera, supervisor_headers, admin_headers, session):
    evidence_id = (await upload(client, supervisor_headers, mp4_bytes())).json()["evidence_id"]
    evidence = (await session.execute(select(Evidence))).scalar_one()
    path = Path(evidence.file_path)

    assert (await client.delete(f"/evidence/{evidence_id}", headers=supervisor_headers)).status_code == 403

    response = await client.delete(f"/evidence/{evidence_id}", headers=admin_headers)
    assert response.status_code == 200 and response.json()["file_deleted"] is True
    assert not path.exists()
    await session.commit()
    assert (await session.execute(select(Evidence))).scalars().all() == []


async def test_evidence_file_download_requires_auth_and_access(client, camera, supervisor_headers, admin_headers,
                                                               operator_headers, outsider, session):
    content = jpeg_bytes()
    body = (await upload(client, supervisor_headers, content, filename="snap.jpg")).json()
    file_url = body["file_url"]

    assert (await client.get(file_url)).status_code == 401

    allowed = await client.get(file_url, headers=admin_headers)
    assert allowed.status_code == 200 and allowed.content == content

    granted = await client.get(file_url, headers=operator_headers)
    assert granted.status_code == 200

    denied = await client.get(file_url, headers=auth_header(outsider))
    assert denied.status_code == 403

    traversal = await client.get("/evidence/files/../../../.env", headers=admin_headers)
    assert traversal.status_code in (403, 404)

    downloads = (await session.execute(select(AuditLog).where(AuditLog.action == "DOWNLOAD_EVIDENCE"))).scalars().all()
    assert {log.status for log in downloads} == {"success", "denied"}


async def test_evidence_file_download_with_query_token(client, camera, supervisor_headers, admin):
    from backend.core.auth import create_access_token, token_claims_for

    body = (await upload(client, supervisor_headers, jpeg_bytes(), filename="snap.jpg")).json()
    token = create_access_token(token_claims_for(admin))
    response = await client.get(body["file_url"], params={"token": token})
    assert response.status_code == 200
