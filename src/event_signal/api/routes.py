"""
API 路由
"""
from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from ..db import get_db
from ..db.models import SignalRepository
from .schemas import SignalResponse, SignalListResponse, StatsResponse, HealthResponse

router = APIRouter(prefix="/api", tags=["signals"])

_service_ref = None


def set_service_ref(service):
    """设置服务引用"""
    global _service_ref
    _service_ref = service


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    ws_connected = False
    pending = 0

    if _service_ref:
        ws_connected = _service_ref.ws is not None
        for handler in _service_ref.handlers.values():
            pending += len(handler.pending_signals)

    return HealthResponse(
        status="ok",
        service="event_signal",
        websocket_connected=ws_connected,
        pending_signals=pending
    )


@router.get("/signals", response_model=SignalListResponse)
async def get_signals(
    symbol: Optional[str] = Query(None, description="交易对"),
    status: Optional[str] = Query(None, description="状态: pending/settled"),
    limit: int = Query(50, ge=1, le=200, description="数量限制"),
):
    """获取信号列表"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        signals = await repo.get_latest(limit)

        if symbol:
            signals = [s for s in signals if s.symbol == symbol]
        if status:
            signals = [s for s in signals if s.status == status]

        return SignalListResponse(
            signals=[SignalResponse.model_validate(s) for s in signals],
            total=len(signals)
        )


@router.get("/signals/latest", response_model=SignalListResponse)
async def get_latest_signals(limit: int = Query(10, ge=1, le=50)):
    """获取最新信号"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        signals = await repo.get_latest(limit)
        return SignalListResponse(
            signals=[SignalResponse.model_validate(s) for s in signals],
            total=len(signals)
        )


@router.get("/signals/{signal_id}", response_model=SignalResponse)
async def get_signal(signal_id: int):
    """获取单个信号"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        signal = await repo.get_by_id(signal_id)
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        return SignalResponse.model_validate(signal)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(days: Optional[int] = Query(None, description="统计天数")):
    """获取统计数据"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        stats = await repo.get_stats(days)
        return StatsResponse(**stats)


@router.get("/stats/today", response_model=StatsResponse)
async def get_today_stats():
    """获取今日统计"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        stats = await repo.get_stats(today_only=True)
        return StatsResponse(**stats)
