"""
API 数据模型
"""
from datetime import datetime, timezone
from typing import Optional, List
from pydantic import BaseModel, field_serializer


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

    @field_serializer('created_at', 'settle_at')
    def serialize_datetime(self, dt: Optional[datetime]) -> Optional[str]:
        """序列化时间为 ISO 格式 + Z 后缀 (UTC)"""
        if dt is None:
            return None
        return dt.isoformat() + 'Z'


class SignalListResponse(BaseModel):
    """信号列表响应"""
    signals: List[SignalResponse]
    total: int


class LevelStats(BaseModel):
    """等级统计"""
    total: int
    wins: int
    losses: int
    win_rate: float
    pnl: float


class StatsResponse(BaseModel):
    """统计响应"""
    total_signals: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    by_level: Optional[dict[str, LevelStats]] = None


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str
    service: str
    websocket_connected: bool
    pending_signals: int
