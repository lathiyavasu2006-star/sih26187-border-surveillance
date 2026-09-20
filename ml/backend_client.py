"""Shared, authenticated HTTP access to the SIH26187 backend.

One token cache for the whole ML process: the login endpoint is rate limited (10/min) and three wrong
passwords lock the account for 30 minutes, so every component must reuse the same session instead of
logging in on its own.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from ml.config import ml_config

logger = logging.getLogger("sih26187.ml.backend")

# Access tokens live 15 minutes on the backend; refresh a little before that.
TOKEN_REFRESH_AFTER_SECONDS = 12 * 60


class BackendAuthError(RuntimeError):
    """Credentials rejected or account locked — retrying would only make things worse."""


class BackendClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self.base_url = (base_url or ml_config.backend_url).rstrip("/")
        self.username = username or ml_config.admin_username
        self.password = password if password is not None else ml_config.admin_password
        self._transport = transport
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._token_obtained_at = 0.0
        self._lock = asyncio.Lock()
        self.user: Dict[str, Any] = {}

    def _client(self, timeout: Optional[float] = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout or ml_config.http_timeout_seconds,
            transport=self._transport,
        )

    # ------------------------------------------------------------------ auth

    async def _login(self) -> None:
        if not self.password:
            raise BackendAuthError("No password configured (set ADMIN_PASSWORD or ML_PASSWORD in .env)")
        async with self._client() as client:
            response = await client.post("/auth/login", data={"username": self.username, "password": self.password})
        if response.status_code in (401, 403, 423):
            raise BackendAuthError(f"Login rejected ({response.status_code}): {response.json().get('detail')}")
        response.raise_for_status()
        body = response.json()
        self._access_token = body["access_token"]
        self._refresh_token = body.get("refresh_token")
        self._token_obtained_at = time.monotonic()
        self.user = body.get("user", {})
        logger.info("Authenticated to backend as %s (role=%s)", self.user.get("username"), self.user.get("role"))

    async def _refresh(self) -> bool:
        if not self._refresh_token:
            return False
        try:
            async with self._client() as client:
                response = await client.post("/auth/refresh", json={"refresh_token": self._refresh_token})
            if response.status_code != 200:
                return False
            self._access_token = response.json()["access_token"]
            self._token_obtained_at = time.monotonic()
            return True
        except httpx.HTTPError:
            return False

    async def get_token(self, force: bool = False) -> str:
        async with self._lock:
            stale = time.monotonic() - self._token_obtained_at > TOKEN_REFRESH_AFTER_SECONDS
            if self._access_token and not force and not stale:
                return self._access_token
            if not (self._access_token and await self._refresh()):
                await self._login()
            return self._access_token

    def invalidate(self) -> None:
        self._access_token = None
        self._token_obtained_at = 0.0

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Authenticated request; re-authenticates once on 401."""
        timeout = kwargs.pop("timeout", None)
        for attempt in range(2):
            token = await self.get_token(force=attempt == 1)
            headers = {**kwargs.pop("headers", {}), "Authorization": f"Bearer {token}"}
            async with self._client(timeout) as client:
                response = await client.request(method, path, headers=headers, **kwargs)
            if response.status_code != 401:
                return response
            self.invalidate()
        return response

    # ------------------------------------------------------------------ API helpers

    async def list_cameras(self) -> List[Dict[str, Any]]:
        cameras: List[Dict[str, Any]] = []
        skip = 0
        while True:
            response = await self.request("GET", "/cameras", params={"skip": skip, "limit": 1000})
            response.raise_for_status()
            page = response.json()
            cameras.extend(page.get("items", []))
            if len(cameras) >= page.get("total", 0) or not page.get("items"):
                return cameras
            skip += len(page["items"])

    async def register_camera(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = await self.request("POST", "/cameras/register", json=payload, timeout=60)
        response.raise_for_status()
        return response.json()

    async def get_zones(self, camera_id: str) -> List[Dict[str, Any]]:
        response = await self.request("GET", f"/zones/{camera_id}")
        response.raise_for_status()
        body = response.json()
        # The backend returns {"items": [...], "total": n}.
        return body.get("items", []) if isinstance(body, dict) else list(body)

    async def upload_evidence(
        self,
        file_path: str,
        camera_id: str,
        alert_id: Optional[str] = None,
        track_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        path = Path(file_path)
        data = {"camera_id": camera_id}
        if alert_id:
            data["alert_id"] = alert_id
        if track_id is not None and track_id >= 0:
            data["track_id"] = str(track_id)
        content = await asyncio.to_thread(path.read_bytes)
        response = await self.request(
            "POST",
            "/evidence/upload",
            data=data,
            files={"file": (path.name, content, "application/octet-stream")},
            timeout=300,
        )
        response.raise_for_status()
        return response.json()

    async def ws_url(self, camera_id: str) -> str:
        token = await self.get_token()
        return f"{ml_config.backend_ws_url.rstrip('/')}/ws/{camera_id}?token={token}"


backend_client = BackendClient()
