"""
Event Signal 服务器
集成信号服务 + API + WebSocket推送
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn

from .config import API_HOST, API_PORT, HORIZON, BREAKEVEN_WINRATE
from .services import SignalService
from .api import router, set_service_ref, ws_manager

server: Optional[SignalService] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期"""
    global server
    server = SignalService()
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

app.include_router(router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点"""
    await ws_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


def run_server(host: str = API_HOST, port: int = API_PORT):
    """运行服务器"""
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
