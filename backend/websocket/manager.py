"""WebSocket connection registry: per-camera rooms plus a per-operator index.

Differences from a naive dict-per-user design:
* one operator may watch several cameras at once, so operator_connections maps user_id -> set of sockets
  (a second tab no longer silently replaces the first, and closing one tab does not drop the others);
* broadcasts are sent concurrently with a timeout, so one slow client cannot stall a camera room;
* dead sockets are pruned from both indexes.
"""
import asyncio
import logging
from typing import Dict, Iterable, Set

from fastapi import WebSocket
from starlette.websockets import WebSocketState

logger = logging.getLogger("sih26187.websocket")

SEND_TIMEOUT_SECONDS = 5.0


class ConnectionManager:
    def __init__(self):
        # Per-camera rooms: {camera_id: set of WebSocket connections}
        self.camera_rooms: Dict[str, Set[WebSocket]] = {}
        # All connected operators: {user_id: set of WebSocket connections}
        self.operator_connections: Dict[str, Set[WebSocket]] = {}
        self._socket_owner: Dict[WebSocket, tuple] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, camera_id: str, user_id: str, accept: bool = True):
        if accept:
            await websocket.accept()
        async with self._lock:
            self.camera_rooms.setdefault(camera_id, set()).add(websocket)
            self.operator_connections.setdefault(user_id, set()).add(websocket)
            self._socket_owner[websocket] = (camera_id, user_id)

    async def disconnect(self, websocket: WebSocket, camera_id: str, user_id: str):
        async with self._lock:
            self._remove_locked(websocket, camera_id, user_id)

    def _remove_locked(self, websocket: WebSocket, camera_id: str = None, user_id: str = None) -> None:
        owner = self._socket_owner.pop(websocket, None)
        if owner:
            camera_id, user_id = owner
        if camera_id in self.camera_rooms:
            self.camera_rooms[camera_id].discard(websocket)
            if not self.camera_rooms[camera_id]:
                del self.camera_rooms[camera_id]
        if user_id in self.operator_connections:
            self.operator_connections[user_id].discard(websocket)
            if not self.operator_connections[user_id]:
                del self.operator_connections[user_id]

    async def _send(self, websocket: WebSocket, data: dict) -> bool:
        try:
            if websocket.client_state != WebSocketState.CONNECTED:
                return False
            await asyncio.wait_for(websocket.send_json(data), timeout=SEND_TIMEOUT_SECONDS)
            return True
        except Exception as exc:  # disconnected, timed out, or serialisation of a closed transport
            logger.debug("WebSocket send failed: %s", exc)
            return False

    async def _send_many(self, sockets: Iterable[WebSocket], data: dict) -> int:
        sockets = list(sockets)
        if not sockets:
            return 0
        results = await asyncio.gather(*(self._send(ws, data) for ws in sockets))
        dead = [ws for ws, ok in zip(sockets, results) if not ok]
        if dead:
            async with self._lock:
                for ws in dead:
                    self._remove_locked(ws)
        return len(sockets) - len(dead)

    async def broadcast_to_camera(self, camera_id: str, data: dict) -> int:
        async with self._lock:
            sockets = set(self.camera_rooms.get(camera_id, set()))
        return await self._send_many(sockets, data)

    async def broadcast_to_all_operators(self, data: dict) -> int:
        async with self._lock:
            sockets = {ws for group in self.operator_connections.values() for ws in group}
        logger.debug("broadcasting %s to %d operator socket(s)", data.get("type"), len(sockets))
        return await self._send_many(sockets, data)

    async def send_to_operator(self, user_id: str, data: dict) -> int:
        async with self._lock:
            sockets = set(self.operator_connections.get(str(user_id), set()))
        return await self._send_many(sockets, data)

    def get_connected_count(self) -> int:
        return len(self.operator_connections)

    def get_connection_count(self) -> int:
        return len(self._socket_owner)

    def get_camera_viewer_count(self, camera_id: str) -> int:
        return len(self.camera_rooms.get(camera_id, set()))

    async def close_all(self, code: int = 1001, reason: str = "Server shutting down") -> int:
        async with self._lock:
            sockets = list(self._socket_owner.keys())
            self.camera_rooms.clear()
            self.operator_connections.clear()
            self._socket_owner.clear()
        for ws in sockets:
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await asyncio.wait_for(ws.close(code=code, reason=reason), timeout=SEND_TIMEOUT_SECONDS)
            except Exception as exc:
                logger.debug("Error closing WebSocket during shutdown: %s", exc)
        return len(sockets)


manager = ConnectionManager()
