"""
Event Signal 服务器
集成信号服务 + API + WebSocket推送
"""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from .config import API_HOST, API_PORT, HORIZON, BREAKEVEN_WINRATE
from .services import SignalService
from .api import router, set_service_ref, ws_manager

server: Optional[SignalService] = None

# 默认数据目录：event_signal/data/
DATA_DIR = os.getenv("DATA_DIR", str(Path(__file__).parent.parent.parent / "data"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期"""
    global server
    server = SignalService(data_dir=DATA_DIR)
    set_service_ref(server)
    await server.start()

    ws_task = asyncio.create_task(server.connect_binance_ws())

    yield

    ws_task.cancel()
    await server.stop()


app = FastAPI(
    title="Event Signal API",
    description="币安事件合约信号系统",
    version="1.0.0",
    lifespan=lifespan
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境改为具体域名
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点"""
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 处理客户端pong响应
            try:
                msg = json.loads(data)
                if msg.get("type") == "pong":
                    ws_manager.update_pong(websocket)
            except Exception:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


def run_server(host: str = API_HOST, port: int = API_PORT):
    """运行服务器"""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
