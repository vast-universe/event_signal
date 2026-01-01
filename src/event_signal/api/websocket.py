"""
WebSocket 实时推送
"""
import json
from typing import Set
from fastapi import WebSocket


class ConnectionManager:
    """WebSocket 连接管理"""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        print(f"📡 WebSocket 客户端连接 (总数: {len(self.active_connections)})")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        print(f"📡 WebSocket 客户端断开 (总数: {len(self.active_connections)})")

    async def broadcast(self, message: dict):
        if not self.active_connections:
            return

        data = json.dumps(message, default=str)
        disconnected = set()

        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.add(connection)

        for conn in disconnected:
            self.active_connections.discard(conn)

    async def send_signal(self, signal_data: dict):
        await self.broadcast({"type": "signal", "data": signal_data})

    async def send_settlement(self, settlement_data: dict):
        await self.broadcast({"type": "settlement", "data": settlement_data})


ws_manager = ConnectionManager()
