"""Generate postman_collection.json (Postman v2.1) covering every endpoint of the API.

    C:\\pythonjarvis\\python.exe E:\\sih26187\\backend\\scripts\\generate_postman_collection.py
"""
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_PATH = PROJECT_ROOT / "postman_collection.json"

SAVE_TOKENS_SCRIPT = [
    "pm.test(\"Login succeeded\", function () {",
    "    pm.response.to.have.status(200);",
    "});",
    "pm.test(\"Save tokens\", function () {",
    "    var json = pm.response.json();",
    "    pm.environment.set(\"token\", json.access_token);",
    "    pm.environment.set(\"refresh_token\", json.refresh_token);",
    "    pm.collectionVariables.set(\"token\", json.access_token);",
    "    pm.collectionVariables.set(\"refresh_token\", json.refresh_token);",
    "    pm.expect(json.token_type).to.eql(\"bearer\");",
    "});",
]

SAVE_ACCESS_TOKEN_SCRIPT = [
    "pm.test(\"Refreshed access token\", function () {",
    "    pm.response.to.have.status(200);",
    "    var json = pm.response.json();",
    "    pm.environment.set(\"token\", json.access_token);",
    "    pm.collectionVariables.set(\"token\", json.access_token);",
    "});",
]

SAVE_CAMERA_SCRIPT = [
    "pm.test(\"Camera registered\", function () {",
    "    pm.response.to.have.status(201);",
    "    var json = pm.response.json();",
    "    pm.environment.set(\"camera_id\", json.camera_id);",
    "    pm.collectionVariables.set(\"camera_id\", json.camera_id);",
    "});",
]

SAVE_ZONE_SCRIPT = [
    "pm.test(\"Zone created\", function () {",
    "    pm.response.to.have.status(201);",
    "    var json = pm.response.json();",
    "    pm.environment.set(\"zone_id\", json.zone_id);",
    "    pm.collectionVariables.set(\"zone_id\", json.zone_id);",
    "});",
]

SAVE_EVIDENCE_SCRIPT = [
    "pm.test(\"Evidence uploaded\", function () {",
    "    pm.response.to.have.status(201);",
    "    var json = pm.response.json();",
    "    pm.environment.set(\"evidence_id\", json.evidence_id);",
    "    pm.collectionVariables.set(\"evidence_id\", json.evidence_id);",
    "    pm.expect(json.file_hash).to.have.lengthOf(64);",
    "});",
]

SAVE_HARDWARE_SCRIPT = [
    "pm.test(\"Hardware registered\", function () {",
    "    pm.response.to.have.status(201);",
    "    var json = pm.response.json();",
    "    pm.environment.set(\"hardware_id\", json.hardware_id);",
    "    pm.collectionVariables.set(\"hardware_id\", json.hardware_id);",
    "});",
]

OK_SCRIPT = [
    "pm.test(\"Successful response\", function () {",
    "    pm.expect(pm.response.code).to.be.oneOf([200, 201, 204]);",
    "});",
]

NO_PASSWORD_LEAK_SCRIPT = [
    "pm.test(\"No secrets in response\", function () {",
    "    var body = pm.response.text();",
    "    pm.expect(body).to.not.include(\"password_hash\");",
    "    pm.expect(body.toLowerCase()).to.not.include(\"hikvision123\");",
    "});",
]


def url(path: str, query: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    clean = path.lstrip("/")
    raw = "{{base_url}}/" + clean
    if query:
        shown = [q for q in query if not q.get("disabled")]
        if shown:
            raw += "?" + "&".join(f"{q['key']}={q['value']}" for q in shown)
    result: Dict[str, Any] = {
        "raw": raw,
        "host": ["{{base_url}}"],
        "path": [segment for segment in clean.split("/") if segment],
    }
    if query:
        result["query"] = query
    return result


def request(
    name: str,
    method: str,
    path: str,
    description: str,
    body: Optional[Any] = None,
    query: Optional[List[Dict[str, Any]]] = None,
    auth: bool = True,
    form: Optional[List[Dict[str, Any]]] = None,
    urlencoded: Optional[List[Dict[str, Any]]] = None,
    test_script: Optional[List[str]] = None,
) -> Dict[str, Any]:
    headers = []
    if auth:
        headers.append({"key": "Authorization", "value": "Bearer {{token}}", "type": "text"})
    if body is not None:
        headers.append({"key": "Content-Type", "value": "application/json", "type": "text"})

    item: Dict[str, Any] = {
        "name": name,
        "request": {
            "method": method,
            "header": headers,
            "url": url(path, query),
            "description": description,
        },
        "response": [],
    }
    if not auth:
        item["request"]["auth"] = {"type": "noauth"}
    if body is not None:
        item["request"]["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    if form is not None:
        item["request"]["body"] = {"mode": "formdata", "formdata": form}
    if urlencoded is not None:
        item["request"]["body"] = {"mode": "urlencoded", "urlencoded": urlencoded}
    if test_script:
        item["event"] = [{"listen": "test", "script": {"type": "text/javascript", "exec": test_script}}]
    return item


def folder(name: str, description: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"name": name, "description": description, "item": items}


def build_collection() -> Dict[str, Any]:
    system = folder("System", "Unauthenticated service endpoints used by monitoring.", [
        request("Root / system info", "GET", "/", "Service name, version and environment.", auth=False,
                test_script=OK_SCRIPT),
        request("Ping (database check)", "GET", "/ping", "Liveness plus a real SELECT 1 against PostgreSQL.",
                auth=False, test_script=OK_SCRIPT),
        request("Health", "GET", "/health",
                "healthy / degraded / down, including camera monitor state. Used by uptime monitoring.",
                auth=False, test_script=OK_SCRIPT),
    ])

    auth_folder = folder("Auth", "JWT login, refresh, profile, logout and password change. All rate limited.", [
        request("Login", "POST", "/auth/login",
                "OAuth2 password form. Saves access_token and refresh_token into the environment. "
                "Three wrong passwords lock the account for 30 minutes.",
                auth=False,
                urlencoded=[
                    {"key": "username", "value": "admin", "type": "text"},
                    {"key": "password", "value": "Admin@SIH2024", "type": "text"},
                ],
                test_script=SAVE_TOKENS_SCRIPT),
        request("Refresh access token", "POST", "/auth/refresh",
                "Exchange the refresh token for a new 15 minute access token.",
                auth=False, body={"refresh_token": "{{refresh_token}}"},
                test_script=SAVE_ACCESS_TOKEN_SCRIPT),
        request("Me (profile)", "GET", "/auth/me", "Current user. Never contains password_hash.",
                test_script=NO_PASSWORD_LEAK_SCRIPT),
        request("Change password", "POST", "/auth/change-password",
                "Requires the current password. New password: 12+ chars with upper, lower, digit and symbol.",
                body={"current_password": "Admin@SIH2024", "new_password": "Border#Secure2026"}),
        request("Logout", "POST", "/auth/logout",
                "Audits the logout and revokes the token when JWT_BLACKLIST_ENABLED=True.",
                body={"refresh_token": "{{refresh_token}}"}, test_script=OK_SCRIPT),
    ])

    cameras = folder("Cameras", "Camera registration, listing, status and stream testing.", [
        request("Register camera", "POST", "/cameras/register",
                "Supervisor or above. The server generates camera_id (CAM-N-001 style) and probes the stream; "
                "a camera whose stream cannot be verified is saved as degraded.",
                body={
                    "name": "North Fence Tower 1",
                    "location_name": "Raxaul Sector Post 4",
                    "gps_lat": 26.98123456,
                    "gps_lng": 84.85123456,
                    "rtsp_url": "rtsp://10.0.0.21:554/stream1",
                    "device_id": None,
                    "camera_type": "standard",
                    "zone_region": "north",
                    "sector_name": "Raxaul",
                },
                test_script=SAVE_CAMERA_SCRIPT),
        request("List cameras", "GET", "/cameras",
                "Cameras with the most unacknowledged alerts first. Non-admins see only their granted cameras/regions.",
                query=[
                    {"key": "zone_region", "value": "north", "disabled": True},
                    {"key": "status", "value": "online", "disabled": True},
                    {"key": "camera_type", "value": "standard", "disabled": True},
                    {"key": "skip", "value": "0"},
                    {"key": "limit", "value": "100"},
                ],
                test_script=OK_SCRIPT),
        request("Host location", "GET", "/cameras/host-location",
                "Supervisor or above. Position of the console host from the Windows location service, used to "
                "locate cameras attached to this machine when the browser has no geolocation. 503 with the reason "
                "when location is off. Cached for a minute (refresh=true bypasses). Audited as READ_HOST_LOCATION.",
                query=[{"key": "refresh", "value": "false", "disabled": True}]),
        request("Get camera", "GET", "/cameras/{{camera_id}}",
                "Camera detail with live viewer count and the last 5 alerts."),
        request("Set camera status", "PATCH", "/cameras/{{camera_id}}/status",
                "Supervisor or above. Setting offline raises a camera_offline alert and notifies every operator.",
                body={"status": "offline"}),
        request("Edit camera", "PATCH", "/cameras/{{camera_id}}",
                "Supervisor or above. Edits name, location, GPS, sector, type or region. Only the fields sent change; "
                "gps_lat and gps_lng must be sent together (null clears both). Audited as EDIT_CAMERA.",
                body={"gps_lat": 32.7266, "gps_lng": 74.857, "location_name": "J&K Border - Sector 7"}),
        request("Test camera stream", "GET", "/cameras/{{camera_id}}/test",
                "Supervisor or above. Opens the stream, measures FPS and returns a base64 JPEG preview."),
        request("Delete camera", "DELETE", "/cameras/{{camera_id}}",
                "Admin only. Refused with 409 when evidence exists; decommissioned (soft) when alerts/events exist."),
    ])

    alerts = folder("Alerts", "Alert triage: list, inspect, acknowledge, statistics and deletion.", [
        request("List alerts", "GET", "/alerts",
                "Unacknowledged first, then newest first.",
                query=[
                    {"key": "camera_id", "value": "{{camera_id}}", "disabled": True},
                    {"key": "alert_type", "value": "intrusion", "disabled": True},
                    {"key": "risk_level", "value": "critical", "disabled": True},
                    {"key": "acknowledged", "value": "false", "disabled": True},
                    {"key": "date_from", "value": "2026-09-01T00:00:00Z", "disabled": True},
                    {"key": "date_to", "value": "2026-12-31T23:59:59Z", "disabled": True},
                    {"key": "skip", "value": "0"},
                    {"key": "limit", "value": "100"},
                ],
                test_script=OK_SCRIPT),
        request("Alert statistics", "GET", "/alerts/stats",
                "Totals for today plus breakdowns by type, risk level, camera and hour (IST).",
                test_script=OK_SCRIPT),
        request("Clear test data", "POST", "/alerts/clear-test-data",
                "Admin only. Acknowledges every unacknowledged alert raised before today (IST) as a false alarm "
                "noted as test data. Nothing is deleted: evidence references alerts."),
        request("Get alert", "GET", "/alerts/{{alert_id}}", "Full alert record including risk reasons."),
        request("Acknowledge alert", "PATCH", "/alerts/{{alert_id}}/acknowledge",
                "Operator or above. acknowledged_by must be your own user_id unless you are admin. "
                "Broadcasts alert_acknowledged over WebSocket.",
                body={
                    "acknowledged_by": "{{user_id}}",
                    "false_alarm": False,
                    "notes": "Patrol dispatched, verified as a stray animal",
                }),
        request("Delete alert", "DELETE", "/alerts/{{alert_id}}",
                "Admin only. Refused with 409 when evidence is attached."),
    ])

    events = folder("Events", "Track history and movement timelines.", [
        request("Active tracks", "GET", "/events/active",
                "Everything currently on camera (is_active=true).",
                query=[{"key": "camera_id", "value": "{{camera_id}}", "disabled": True},
                       {"key": "limit", "value": "100"}],
                test_script=OK_SCRIPT),
        request("List events", "GET", "/events", "Filterable event history, newest first.",
                query=[
                    {"key": "camera_id", "value": "{{camera_id}}", "disabled": True},
                    {"key": "object_class", "value": "person", "disabled": True},
                    {"key": "is_active", "value": "false", "disabled": True},
                    {"key": "date_from", "value": "2026-09-01T00:00:00Z", "disabled": True},
                    {"key": "skip", "value": "0"},
                    {"key": "limit", "value": "100"},
                ],
                test_script=OK_SCRIPT),
        request("Track history", "GET", "/events/{{track_id}}",
                "Every event for one track id, with totals. Add camera_id because track ids repeat per camera.",
                query=[{"key": "camera_id", "value": "{{camera_id}}", "disabled": True}]),
        request("Track timeline", "GET", "/events/{{track_id}}/timeline",
                "Ordered positions with zone and risk at each point, from tracked_objects.",
                query=[{"key": "camera_id", "value": "{{camera_id}}", "disabled": True}]),
    ])

    evidence = folder("Evidence", "Chain of custody: upload, verify, archive, delete.", [
        request("Upload evidence", "POST", "/evidence/upload",
                "Supervisor or above. Multipart upload, max 500 MB. The SHA-256 is computed while the bytes are "
                "streamed to disk and the magic bytes must match the extension.",
                form=[
                    {"key": "file", "type": "file", "src": [], "description": "MP4/AVI/MOV/MKV or JPG/JPEG/PNG"},
                    {"key": "camera_id", "value": "{{camera_id}}", "type": "text"},
                    {"key": "alert_id", "value": "", "type": "text", "disabled": True},
                    {"key": "track_id", "value": "", "type": "text", "disabled": True},
                ],
                test_script=SAVE_EVIDENCE_SCRIPT),
        request("List evidence", "GET", "/evidence", "Newest first, with a file_url and file_exists flag.",
                query=[
                    {"key": "camera_id", "value": "{{camera_id}}", "disabled": True},
                    {"key": "evidence_type", "value": "snapshot", "disabled": True},
                    {"key": "alert_id", "value": "{{alert_id}}", "disabled": True},
                    {"key": "is_hot_storage", "value": "true", "disabled": True},
                    {"key": "skip", "value": "0"},
                    {"key": "limit", "value": "100"},
                ],
                test_script=OK_SCRIPT),
        request("Get evidence", "GET", "/evidence/{{evidence_id}}", "Evidence record plus its authenticated file URL."),
        request("Verify evidence integrity", "GET", "/evidence/{{evidence_id}}/verify",
                "Recomputes the SHA-256 on disk and compares it: valid / tampered / missing."),
        request("Play evidence video", "GET", "/evidence/{{evidence_id}}/playback",
                "Browser-playable video. Returns the original when it is H.264/VP8, otherwise a cached H.264 preview "
                "derived from it (the evidence file and its SHA-256 are never modified)."),
        request("Analyse evidence video", "POST", "/evidence/{{evidence_id}}/analyze",
                "Supervisor or above. Runs YOLOv8x over the video in a worker process; high-risk and critical "
                "findings become alerts with SHA-256-verified snapshots. One analysis runs at a time (409 otherwise).",
                test_script=[
                    "pm.test('accepted', () => pm.response.to.have.status(202));",
                    "pm.collectionVariables.set('analysis_job_id', pm.response.json().job_id);",
                ]),
        request("Evidence analysis status", "GET", "/evidence/{{evidence_id}}/analysis-status",
                "Latest analysis job for this evidence: status, percent, live counts and the final summary."),
        request("Analysis job progress", "GET", "/evidence/analysis-jobs/{{analysis_job_id}}",
                "Progress of any analysis job by id (poll every 2 s)."),
        request("Analyse standalone video", "POST", "/evidence/analyze-standalone",
                "Supervisor or above. Analyses a video that belongs to no camera: no zones, and no alerts or evidence "
                "are stored. The uploaded file is deleted when the analysis ends.",
                form=[{"key": "file", "type": "file", "src": [], "description": "MP4/AVI/MOV/MKV, max 500 MB"}]),
        request("Download evidence file", "GET", "/evidence/files/snapshots/example.jpg",
                "Authenticated static serving. Works with the Bearer header or ?token= for <img> tags. "
                "Every download is audited.",
                query=[{"key": "token", "value": "{{token}}", "disabled": True}]),
        request("Archive evidence", "POST", "/evidence/{{evidence_id}}/archive",
                "Admin only. Verifies the hash, then moves the file to cold storage."),
        request("Delete evidence", "DELETE", "/evidence/{{evidence_id}}", "Admin only. Removes the record and the file."),
    ])

    zones = folder("Zones", "Detection zones. risk_bonus is policy, derived from zone_type.", [
        request("Create zone", "POST", "/zones",
                "Supervisor or above. Polygon needs 3+ integer pixel points. risk_bonus is set from zone_type: "
                "public 0, buffer 10, sensitive 30, restricted 50, no_mans_land 100.",
                body={
                    "camera_id": "{{camera_id}}",
                    "zone_name": "Fence Line North",
                    "zone_type": "restricted",
                    "polygon": [[100, 100], [500, 100], [500, 400], [100, 400]],
                    "loiter_threshold_seconds": 30,
                    "night_rules": {"multiplier": 1.5, "start": 22, "end": 5},
                    "allowed_persons": [],
                    "color_hex": "#ff0000",
                    "is_active": True,
                },
                test_script=SAVE_ZONE_SCRIPT),
        request("List zones", "GET", "/zones", "All zones visible to you.",
                query=[
                    {"key": "camera_id", "value": "{{camera_id}}", "disabled": True},
                    {"key": "zone_type", "value": "restricted", "disabled": True},
                    {"key": "is_active", "value": "true"},
                ],
                test_script=OK_SCRIPT),
        request("Zones for camera", "GET", "/zones/{{camera_id}}", "Active zones of one camera.",
                query=[{"key": "include_inactive", "value": "false"}]),
        request("Update zone", "PUT", "/zones/{{zone_id}}",
                "Supervisor or above. Broadcasts zone_update to everyone watching the camera.",
                body={
                    "zone_name": "Fence Line North (revised)",
                    "polygon": [[120, 120], [520, 120], [520, 420], [120, 420]],
                    "loiter_threshold_seconds": 20,
                    "night_rules": {"multiplier": 2.0, "start": 21, "end": 6},
                }),
        request("Delete zone (soft)", "DELETE", "/zones/{{zone_id}}",
                "Supervisor or above. Sets is_active=false and broadcasts zone_deleted."),
    ])

    hardware = folder("Hardware", "Hardware registry. Connection passwords are always masked in responses.", [
        request("Hardware status board", "GET", "/hardware/status",
                "Every hardware type with connected/disconnected/error/standby counts and masked configs.",
                test_script=NO_PASSWORD_LEAK_SCRIPT),
        request("Register hardware", "POST", "/hardware/register",
                "Admin only. connection_config is stored as given but never returned in clear text.",
                body={
                    "hardware_type": "thermal_camera",
                    "name": "Thermal Unit North 1",
                    "model_number": "FLIR-A310",
                    "manufacturer": "FLIR",
                    "connection_config": {
                        "rtsp_url": "rtsp://10.0.0.31:554/thermal",
                        "username": "operator",
                        "password": "hikvision123",
                    },
                    "status": "standby",
                    "camera_id": "{{camera_id}}",
                    "capabilities": ["thermal", "night_vision"],
                    "firmware_version": "2.4.1",
                    "notes": "Mounted on the north watchtower",
                },
                test_script=SAVE_HARDWARE_SCRIPT),
        request("Test hardware connection", "POST", "/hardware/{{hardware_id}}/test-connection",
                "Supervisor or above. Cameras are probed with OpenCV, other devices with an HTTP request. "
                "Updates status and last_seen."),
        request("Update hardware", "PATCH", "/hardware/{{hardware_id}}",
                "Admin only. Sending back the masked '***' keeps the stored secret unchanged.",
                body={
                    "notes": "Firmware updated during the September maintenance window",
                    "capabilities": ["thermal", "night_vision", "ptz"],
                    "connection_config": {"rtsp_url": "rtsp://10.0.0.31:554/thermal", "password": "***"},
                }),
        request("Delete hardware", "DELETE", "/hardware/{{hardware_id}}", "Admin only."),
    ])

    stats = folder("Stats", "Live metrics, audit trail and health history.", [
        request("Live stats", "GET", "/stats",
                "Detection counts for today, alert and camera counts, FPS, CPU/RAM/GPU/disk and uptime.",
                test_script=OK_SCRIPT),
        request("Audit log", "GET", "/stats/audit-log",
                "Admin only. Every action in the system is recorded here.",
                query=[
                    {"key": "action", "value": "LOGIN_SUCCESS", "disabled": True},
                    {"key": "status", "value": "denied", "disabled": True},
                    {"key": "user_id", "value": "{{user_id}}", "disabled": True},
                    {"key": "date_from", "value": "2026-09-01T00:00:00Z", "disabled": True},
                    {"key": "skip", "value": "0"},
                    {"key": "limit", "value": "100"},
                ],
                test_script=OK_SCRIPT),
        request("System health history", "GET", "/stats/system-health-history",
                "Admin or regional head. Samples recorded every 60 seconds by the background service.",
                query=[{"key": "hours", "value": "24"},
                       {"key": "server_name", "value": "local", "disabled": True}],
                test_script=OK_SCRIPT),
    ])

    websocket = folder(
        "WebSocket",
        "Live stream socket: ws://localhost:8000/ws/{camera_id}?token=<JWT>\n\n"
        "Postman v2.1 collections cannot carry WebSocket requests, so create a WebSocket request in Postman "
        "(New > WebSocket) with the URL above and use the message examples below.\n\n"
        "Send ping:\n"
        '{\"type\": \"ping\"}\n\n'
        "Send frame_data (admin/supervisor only — this is the Week 3 ML pipeline channel):\n"
        '{\"type\": \"frame_data\", \"camera_id\": \"CAM-N-001\", \"frame\": \"<base64 jpeg>\", '
        '\"detections\": [{\"track_id\": 7, \"object_class\": \"person\", \"confidence\": 0.91, '
        '\"cx\": 320, \"cy\": 240, \"bbox_x1\": 300, \"bbox_y1\": 200, \"bbox_x2\": 340, \"bbox_y2\": 280, '
        '\"in_fence\": true, \"zone_name\": \"Fence Line North\", \"zone_type\": \"restricted\", '
        '\"loitering\": false, \"time_in_zone_seconds\": 4, \"direction\": \"north\", '
        '\"risk_score\": 85, \"risk_level\": \"critical\"}], '
        '\"alerts\": [{\"alert_type\": \"intrusion\", \"risk_score\": 85, '
        '\"risk_reasons\": [\"restricted zone\", \"night hours\"]}], '
        '\"stats\": {\"people_count\": 1, \"vehicle_count\": 0, \"animal_count\": 0, '
        '\"active_alerts\": 1, \"fps\": 15.2}, \"timestamp\": \"2026-09-16T18:30:00Z\"}\n\n'
        "Send acknowledge:\n"
        '{\"type\": \"acknowledge\", \"alert_id\": \"ALT-20260916183000-a1b2c3d4e5f6a7b8\", '
        '\"false_alarm\": false, \"notes\": \"Patrol dispatched\"}\n\n'
        "Received types: connected, pong, frame_update, frame_ack, acknowledge_ok, error, "
        "camera_offline, camera_online, critical_alert, alert_acknowledged, zone_update, zone_deleted, "
        "neighbor_offline_alert.",
        [
            request("WebSocket docs (placeholder)", "GET", "/docs",
                    "Opens Swagger UI. The WebSocket contract itself is documented in this folder's description.",
                    auth=False),
        ],
    )

    return {
        "info": {
            "_postman_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "sih26187-border-surveillance-api")),
            "name": "SIH26187 Border Surveillance API",
            "description": (
                "Ministry of Home Affairs / SSB AI border surveillance backend (Week 2).\n\n"
                "Run Auth > Login first: it stores access_token and refresh_token as variables that every other "
                "request uses. Access tokens expire after 15 minutes; use Auth > Refresh access token.\n\n"
                "Roles: admin > regional_head > supervisor > operator. Delete operations are admin only."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{token}}", "type": "string"}]},
        "event": [
            {"listen": "prerequest", "script": {"type": "text/javascript", "exec": [
                "if (!pm.collectionVariables.get(\"base_url\")) {",
                "    pm.collectionVariables.set(\"base_url\", \"http://localhost:8000\");",
                "}",
            ]}},
        ],
        "variable": [
            {"key": "base_url", "value": "http://localhost:8000", "type": "string"},
            {"key": "token", "value": "", "type": "string"},
            {"key": "refresh_token", "value": "", "type": "string"},
            {"key": "user_id", "value": "", "type": "string"},
            {"key": "camera_id", "value": "CAM-N-001", "type": "string"},
            {"key": "alert_id", "value": "", "type": "string"},
            {"key": "evidence_id", "value": "", "type": "string"},
            {"key": "zone_id", "value": "", "type": "string"},
            {"key": "hardware_id", "value": "", "type": "string"},
            {"key": "track_id", "value": "1", "type": "string"},
            {"key": "analysis_job_id", "value": "", "type": "string"},
        ],
        "item": [system, auth_folder, cameras, alerts, events, evidence, zones, hardware, stats, websocket],
    }


def main() -> None:
    collection = build_collection()
    OUTPUT_PATH.write_text(json.dumps(collection, indent=2), encoding="utf-8")
    folders = len(collection["item"])
    requests = sum(len(f["item"]) for f in collection["item"])
    print(f"Wrote {OUTPUT_PATH} ({folders} folders, {requests} requests)")


if __name__ == "__main__":
    main()
