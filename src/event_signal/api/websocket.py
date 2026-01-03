"""
WebSocket 实时推送
"""
import asyncio
import json
import time
from typing import Dict
from fastapi import WebSocket


class ConnectionManager:
    """WebSocket 连接管理"""

    def __init__(self):
        self.active_connections: Dict[WebSocket, float] = {}  # ws -> last_pong_time
        self._heartbeat_task = None

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[websocket] = time.time()
        print(f"📡 WebSocket 客户端连接 (总数: {len(self.active_connections)})")
        
        # 启动心跳检测
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)
        print(f"📡 WebSocket 客户端断开 (总数: {len(self.active_connections)})")

    def update_pong(self, websocket: WebSocket):
        """更新客户端心跳时间"""
        if websocket in self.active_connections:
            self.active_connections[websocket] = time.time()

    async def _heartbeat_loop(self):
        """心跳检测循环，每30秒发送ping，清理超时连接"""
        while True:
            try:
                await asyncio.sleep(30)
                if not self.active_connections:
                    continue
                
                now = time.time()
                disconnected = []
                
                for ws, last_pong in list(self.active_connections.items()):
                    # 超过90秒无响应，断开连接
                    if now - last_pong > 90:
                        disconnected.append(ws)
                        continue
                    
                    # 发送ping
                    try:
                        await ws.send_json({"type": "ping", "ts": int(now * 1000)})
                    except Exception:
                        disconnected.append(ws)
                
                for ws in disconnected:
                    self.active_connections.pop(ws, None)
                    try:
                        await ws.close()
                    except Exception:
                        pass
                
                if disconnected:
                    print(f"📡 清理 {len(disconnected)} 个超时连接 (剩余: {len(self.active_connections)})")
                    
            except Exception as e:
                print(f"⚠️ 心跳检测错误: {e}")

    async def _send_to_one(self, ws: WebSocket, data: str):
        """发送到单个客户端（不等待）"""
        try:
            await ws.send_text(data)
        except Exception:
            self.active_connections.pop(ws, None)

    async def broadcast(self, message: dict):
        """异步广播，不阻塞"""
        if not self.active_connections:
            return

        data = json.dumps(message, default=str)
        
        # 并发发送到所有客户端
        tasks = [self._send_to_one(ws, data) for ws in list(self.active_connections.keys())]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def broadcast_nowait(self, message: dict):
        """非阻塞广播（fire and forget）"""
        if self.active_connections:
            asyncio.create_task(self.broadcast(message))

    async def send_signal(self, signal_data: dict):
        """发送信号（非阻塞）"""
        self.broadcast_nowait({"type": "signal", "data": signal_data})

    async def send_settlement(self, settlement_data: dict):
        """发送结算（非阻塞）"""
        self.broadcast_nowait({"type": "settlement", "data": settlement_data})

    async def send_ticker(self, ticker_data: dict):
        """发送行情（非阻塞）"""
        self.broadcast_nowait({"type": "ticker", "data": ticker_data})


ws_manager = ConnectionManager()
