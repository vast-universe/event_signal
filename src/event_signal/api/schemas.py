"""
API 数据模型
"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel


class SignalResponse(BaseModel):
    """信号响应"""
    id: int
    symbol: str
    direction: str
    level: str
    confidence: float
    entry_price: float
    bet_amount: float
    created_at: datetime
    settle_at: Optional[datetime] = None
    settle_price: Optional[float] = None
    is_win: Optional[bool] = None
    pnl: Optional[float] = None
    status: str

    class Config:
        from_attributes = True


class SignalListResponse(BaseModel):
    """信号列表响应"""
    signals: List[SignalResponse]
    total: int


class StatsResponse(BaseModel):
    """统计响应"""
    total_signals: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    service: str
    websocket_connected: bool
    pending_signals: int
