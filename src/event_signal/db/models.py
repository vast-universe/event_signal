"""
数据库模型
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Index, select, func

from .database import Base

# 北京时区 UTC+8
BEIJING_TZ = timezone(timedelta(hours=8))


class Signal(Base):
    """信号记录表"""
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    level = Column(String(5), nullable=False)
    confidence = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    bet_amount = Column(Float, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    settle_at = Column(DateTime, nullable=True)
    settle_price = Column(Float, nullable=True)
    is_win = Column(Boolean, nullable=True)
    pnl = Column(Float, nullable=True)
    status = Column(String(20), default="pending")

    __table_args__ = (
        Index('idx_symbol_status', 'symbol', 'status'),
        Index('idx_created_at', 'created_at'),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "level": self.level,
            "confidence": self.confidence,
            "entry_price": self.entry_price,
            "bet_amount": self.bet_amount,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "settle_at": self.settle_at.isoformat() if self.settle_at else None,
            "settle_price": self.settle_price,
            "is_win": self.is_win,
            "pnl": self.pnl,
            "status": self.status,
        }


class SignalRepository:
    """信号数据访问层"""

    def __init__(self, session):
        self.session = session

    async def create(self, signal_data: dict) -> Signal:
        signal = Signal(**signal_data)
        self.session.add(signal)
        await self.session.flush()
        return signal

    async def update_settled(self, signal_id: int, settle_price: float,
                             is_win: bool, pnl: float):
        result = await self.session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        signal = result.scalar_one_or_none()
        if signal:
            signal.settle_at = datetime.utcnow()
            signal.settle_price = settle_price
            signal.is_win = is_win
            signal.pnl = pnl
            signal.status = "settled"

    async def get_pending(self, symbol: str = None) -> list:
        query = select(Signal).where(Signal.status == "pending")
        if symbol:
            query = query.where(Signal.symbol == symbol)
        result = await self.session.execute(query)
        return result.scalars().all()

    async def get_latest(self, limit: int = 20) -> list:
        result = await self.session.execute(
            select(Signal)
            .order_by(Signal.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    async def get_by_id(self, signal_id: int) -> Optional[Signal]:
        result = await self.session.execute(
            select(Signal).where(Signal.id == signal_id)
        )
        return result.scalar_one_or_none()

    async def get_stats(self, days: int = None, today_only: bool = False) -> dict:
        query = select(
            func.count(Signal.id).label('total'),
            func.sum(func.cast(Signal.is_win, Integer)).label('wins'),
            func.sum(Signal.pnl).label('total_pnl'),
        ).where(Signal.status == "settled")

        start_date = None
        if today_only:
            # 北京时间今天0点 (转换为UTC存储)
            now_beijing = datetime.now(BEIJING_TZ)
            today_start_beijing = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
            start_date = today_start_beijing.astimezone(timezone.utc).replace(tzinfo=None)
            query = query.where(Signal.created_at >= start_date)
        elif days:
            start_date = datetime.utcnow() - timedelta(days=days)
            query = query.where(Signal.created_at >= start_date)

        result = await self.session.execute(query)
        row = result.one()

        total = row.total or 0
        wins = row.wins or 0
        total_pnl = row.total_pnl or 0

        # 按等级统计
        by_level = {}
        for level in ['S', 'A', 'B', 'C']:
            level_query = select(
                func.count(Signal.id).label('total'),
                func.sum(func.cast(Signal.is_win, Integer)).label('wins'),
                func.sum(Signal.pnl).label('pnl'),
            ).where(Signal.status == "settled", Signal.level == level)

            if today_only:
                level_query = level_query.where(Signal.created_at >= start_date)
            elif days:
                level_query = level_query.where(Signal.created_at >= start_date)

            level_result = await self.session.execute(level_query)
            level_row = level_result.one()

            level_total = level_row.total or 0
            level_wins = level_row.wins or 0
            level_pnl = level_row.pnl or 0

            by_level[level] = {
                "total": level_total,
                "wins": level_wins,
                "losses": level_total - level_wins,
                "win_rate": level_wins / level_total if level_total > 0 else 0,
                "pnl": level_pnl,
            }

        return {
            "total_signals": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": wins / total if total > 0 else 0,
            "total_pnl": total_pnl,
            "by_level": by_level,
        }
