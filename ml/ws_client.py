"""Resilient WebSocket publisher to the backend `/ws/{camera_id}` endpoint.

* reconnects with backoff on any disconnect; a policy close (1008, e.g. expired token) forces re-login;
* a reader task drains everything the backend sends (frame_ack, errors and the broadcasts this socket
  receives as a member of the camera room). Without it the client's receive queue fills up, TCP back
  pressure stalls the server's sends and the backend eventually drops this connection;
* send failures never raise into the pipeline: frames are dropped while disconnected, but alert messages
  are queued and re-sent after reconnection so no alert is lost;
* delivery is confirmed, not assumed: the backend answers every frame_data with exactly one frame_ack (or
  error), in order. A socket the server has already closed can still accept `send()` without raising, so
  alerts without an acknowledgement are re-queued when the connection drops and re-sent (the backend
  ignores duplicate alert ids).
"""
import asyncio
import json
import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatusCode

from ml.backend_client import BackendAuthError, BackendClient, backend_client

logger = logging.getLogger("sih26187.ml.ws")

MAX_MESSAGE_BYTES = 32 * 1024 * 1024
PENDING_ALERT_LIMIT = 500
UNACKED_LIMIT = 2000
POLICY_VIOLATION = 1008


class WSClient:
    def __init__(self, camera_id: str, client: Optional[BackendClient] = None, url_override: Optional[str] = None):
        self.camera_id = camera_id
        self.client = client or backend_client
        self.url_override = url_override
        self.ws = None
        self._connected = False
        self._reconnect_delay = 5
        self._max_reconnect_delay = 30
        self._attempts = 0
        self._reader_task: Optional[asyncio.Task] = None
        self._connect_lock = asyncio.Lock()
        self._closing = False
        self.pending_alerts: Deque[Dict[str, Any]] = deque(maxlen=PENDING_ALERT_LIMIT)
        # One entry per message sent on the current socket, awaiting its frame_ack/error (None = plain frame).
        self._unacked: Deque[Optional[Dict[str, Any]]] = deque(maxlen=UNACKED_LIMIT)
        self.acknowledged_messages = 0
        self.resent_alerts = 0
        self.last_ack: Optional[Dict[str, Any]] = None
        self.errors: Deque[Dict[str, Any]] = deque(maxlen=50)
        self.sent_messages = 0
        self.dropped_frames = 0
        self.reconnects = 0
        self.may_ingest: Optional[bool] = None
        self._ever_connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self.ws is not None

    async def get_token(self) -> str:
        return await self.client.get_token()

    async def _uri(self) -> str:
        return self.url_override or await self.client.ws_url(self.camera_id)

    async def connect(self, max_attempts: Optional[int] = None) -> bool:
        """Connect (retrying with backoff). Returns False only if max_attempts is exhausted."""
        async with self._connect_lock:
            if self.connected:
                return True
            attempts = 0
            while not self._closing:
                try:
                    uri = await self._uri()
                    self.ws = await websockets.connect(
                        uri, ping_interval=20, ping_timeout=20, close_timeout=5,
                        max_size=MAX_MESSAGE_BYTES, open_timeout=15,
                    )
                    hello = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=10))
                    if hello.get("type") != "connected":
                        raise ConnectionError(f"unexpected handshake message: {hello}")
                    self.may_ingest = bool(hello.get("may_ingest_frames"))
                    if not self.may_ingest:
                        logger.error("[%s] account role %s may not publish frames", self.camera_id, hello.get("role"))
                    self._connected = True
                    if self._ever_connected:  # any successful connection after the first is a reconnect
                        self.reconnects += 1
                    self._ever_connected = True
                    self._attempts = 0
                    self._reader_task = asyncio.create_task(self._reader(), name=f"ws-reader-{self.camera_id}")
                    logger.info("[%s] WebSocket connected", self.camera_id)
                    await self._flush_pending_alerts()
                    return True
                except BackendAuthError:
                    raise
                except Exception as exc:
                    self._connected = False
                    code = getattr(getattr(exc, "rcvd", None), "code", None)
                    if isinstance(exc, InvalidStatusCode) and exc.status_code in (401, 403):
                        code = POLICY_VIOLATION
                    if code == POLICY_VIOLATION:
                        self.client.invalidate()
                    attempts += 1
                    self._attempts += 1
                    if max_attempts is not None and attempts >= max_attempts:
                        logger.warning("[%s] WS connect failed after %d attempt(s): %s", self.camera_id, attempts, exc)
                        return False
                    delay = min(self._reconnect_delay * (2 ** min(self._attempts - 1, 3)), self._max_reconnect_delay)
                    logger.warning("[%s] WS connect failed (%s), retry in %.0fs", self.camera_id, exc, delay)
                    await asyncio.sleep(delay)
            return False

    async def _reader(self) -> None:
        ws = self.ws
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except (TypeError, ValueError):
                    continue
                kind = message.get("type")
                if kind in ("frame_ack", "error") and self._unacked:
                    self._unacked.popleft()
                    self.acknowledged_messages += 1
                if kind == "frame_ack":
                    self.last_ack = message
                    for problem in message.get("problems") or []:
                        logger.warning("[%s] backend reported: %s", self.camera_id, problem)
                elif kind == "error":
                    self.errors.append(message)
                    logger.warning("[%s] backend error: %s", self.camera_id, message.get("message"))
                elif kind in ("camera_offline", "critical_alert", "zone_update", "zone_deleted"):
                    logger.info("[%s] backend notification: %s", self.camera_id, kind)
                # frame_update echoes of our own frames and other broadcasts are simply drained.
        except ConnectionClosed as exc:
            if exc.rcvd and exc.rcvd.code == POLICY_VIOLATION:
                self.client.invalidate()
            logger.warning("[%s] WebSocket closed: %s", self.camera_id, exc)
        except Exception as exc:
            logger.warning("[%s] WebSocket reader stopped: %s", self.camera_id, exc)
        finally:
            if self.ws is ws:
                self._connected = False
                self._requeue_unacknowledged()

    def _requeue_unacknowledged(self) -> None:
        """Alerts sent on a dead socket without an acknowledgement go back to the front of the queue."""
        orphaned = [payload for payload in self._unacked if payload is not None]
        self._unacked.clear()
        if orphaned:
            self.pending_alerts.extendleft(reversed(orphaned))
            self.resent_alerts += len(orphaned)
            logger.warning("[%s] %d alert message(s) were not acknowledged; queued for re-send", self.camera_id, len(orphaned))

    async def _flush_pending_alerts(self) -> None:
        while self.pending_alerts and self.connected:
            payload = self.pending_alerts[0]
            try:
                await self.ws.send(json.dumps(payload))
                self.pending_alerts.popleft()
                self._unacked.append(payload)
                self.sent_messages += 1
            except Exception as exc:
                logger.warning("[%s] could not flush queued alert: %s", self.camera_id, exc)
                self._connected = False
                return

    async def send_frame(self, payload: Dict[str, Any], critical: bool = False) -> bool:
        """Send a frame_data message. `critical` messages (alerts) are queued if the socket is down."""
        if not self.connected:
            if critical:
                self.pending_alerts.append(payload)
            else:
                self.dropped_frames += 1
            asyncio.get_running_loop().create_task(self._background_reconnect())
            return False
        try:
            await self.ws.send(json.dumps(payload))
            self._unacked.append(payload if payload.get("alerts") else None)
            self.sent_messages += 1
            return True
        except Exception as exc:
            logger.warning("[%s] WS send error: %s", self.camera_id, exc)
            self._connected = False
            if critical:
                self.pending_alerts.append(payload)
            else:
                self.dropped_frames += 1
            asyncio.get_running_loop().create_task(self._background_reconnect())
            return False

    async def _background_reconnect(self) -> None:
        if self._connect_lock.locked() or self._closing:
            return
        try:
            await self.connect()
        except BackendAuthError as exc:
            logger.error("[%s] cannot reconnect, authentication failed: %s", self.camera_id, exc)

    def undelivered_alerts(self) -> int:
        return len(self.pending_alerts) + sum(1 for payload in self._unacked if payload is not None)

    async def flush_pending(self, max_attempts: int = 3, timeout: float = 20.0) -> int:
        """Before shutdown: send queued alerts and wait until the backend has acknowledged every alert.
        Returns how many alerts are still undelivered."""
        deadline = time.monotonic() + timeout
        attempts = 0
        while self.undelivered_alerts() and not self._closing and time.monotonic() < deadline:
            try:
                if not self.connected:
                    if attempts >= max_attempts:
                        break
                    attempts += 1
                    await self.connect(max_attempts=1)  # a successful connect flushes the queue
                elif self.pending_alerts:
                    await self._flush_pending_alerts()
                else:
                    await asyncio.sleep(0.05)  # waiting for acknowledgements
            except BackendAuthError as exc:
                logger.error("[%s] cannot flush queued alerts: %s", self.camera_id, exc)
                break
        return self.undelivered_alerts()

    async def close(self) -> None:
        self._closing = True
        self._connected = False
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
