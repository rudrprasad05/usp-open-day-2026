from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, set[WebSocket]] = defaultdict(set)
        self.lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, role: str) -> None:
        await websocket.accept()
        async with self.lock:
            self.connections[role].add(websocket)
        logger.info("%s client connected", role)

    async def disconnect(self, websocket: WebSocket, role: str) -> None:
        async with self.lock:
            self.connections[role].discard(websocket)
        logger.info("%s client disconnected", role)

    async def send_role(self, role: str, payload: dict) -> None:
        stale: list[WebSocket] = []
        for socket in tuple(self.connections[role]):
            try:
                await socket.send_json(payload)
            except Exception:
                stale.append(socket)
        if stale:
            async with self.lock:
                for socket in stale:
                    self.connections[role].discard(socket)

