"""
交易策略 - 基于超买超卖 + River在线学习
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..config import (
    OVERBOUGHT, OVERSOLD,
    SIGNAL_THRESHOLDS, VOL_SPIKE_MAX, SIGNAL_COOLDOWN,
    get_signal_level, get_bet_amount
)


@dataclass
class Signal:
    """交易信号"""
    symbol: str
    direction: str      # "UP" 或 "DOWN"
    level: str          # "S", "A", "B", "C"
    price: float
    confidence: float
    bet_amount: float
    timestamp: datetime
    details: str = ""

    def __str__(self):
        d = "🔴做空" if self.direction == "DOWN" else "🟢做多"
        return (f"🚨 {self.symbol} {d} | {self.level}级 | "
                f"${self.price:,.2f} | 置信度:{self.confidence:.1%} | 下注:{self.bet_amount}U")


class Strategy:
    """交易策略"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.last_signal_time: Optional[datetime] = None
        self.signals_this_hour: list = []
        self.consecutive_losses = 0
        self.results: list = []
        self.skipped_vol_spike = 0  # 因成交量暴涨跳过的信号数
        self.skipped_cooldown = 0   # 因冷却时间跳过的信号数
        # 冷却时间追踪
        self.last_long_time: Optional[datetime] = None
        self.last_short_time: Optional[datetime] = None

    def check(self, price: float, features: dict,
              prob_down: float, prob_up: float) -> Optional[Signal]:
        """检查是否产生信号"""
        now = datetime.now()
        cooldown = timedelta(minutes=SIGNAL_COOLDOWN)

        if not features:
            return None

        rsi6 = features.get('rsi6', 50)
        bb_pct = features.get('bb_pct', 0.5)
        vol_spike = features.get('vol_spike', 1.0)

        signal = None

        # 超买区域 → 做空信号
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            level = get_signal_level(prob_down)
            if level:
                # vol_spike 过滤：成交量暴涨时跳过
                if vol_spike > VOL_SPIKE_MAX:
                    self.skipped_vol_spike += 1
                    print(f"\n⚠️ {self.symbol} 做空信号跳过: vol_spike={vol_spike:.1f} > {VOL_SPIKE_MAX}")
                    return None
                
                # 冷却时间过滤：同方向信号间隔
                if self.last_short_time and (now - self.last_short_time) < cooldown:
                    self.skipped_cooldown += 1
                    print(f"\n⚠️ {self.symbol} 做空信号跳过: 冷却中 ({SIGNAL_COOLDOWN}分钟)")
                    return None
                
                signal = Signal(
                    symbol=self.symbol,
                    direction="DOWN",
                    level=level,
                    price=price,
                    confidence=prob_down,
                    bet_amount=get_bet_amount(level),
                    timestamp=now,
                    details=f"RSI6={rsi6:.0f} BB={bb_pct:.2f} P(跌)={prob_down:.1%} vol_spike={vol_spike:.1f}"
                )
                self.last_short_time = now

        # 超卖区域 → 做多信号
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            level = get_signal_level(prob_up)
            if level:
                # vol_spike 过滤：成交量暴涨时跳过
                if vol_spike > VOL_SPIKE_MAX:
                    self.skipped_vol_spike += 1
                    print(f"\n⚠️ {self.symbol} 做多信号跳过: vol_spike={vol_spike:.1f} > {VOL_SPIKE_MAX}")
                    return None
                
                # 冷却时间过滤：同方向信号间隔
                if self.last_long_time and (now - self.last_long_time) < cooldown:
                    self.skipped_cooldown += 1
                    print(f"\n⚠️ {self.symbol} 做多信号跳过: 冷却中 ({SIGNAL_COOLDOWN}分钟)")
                    return None
                
                signal = Signal(
                    symbol=self.symbol,
                    direction="UP",
                    level=level,
                    price=price,
                    confidence=prob_up,
                    bet_amount=get_bet_amount(level),
                    timestamp=now,
                    details=f"RSI6={rsi6:.0f} BB={bb_pct:.2f} P(涨)={prob_up:.1%} vol_spike={vol_spike:.1f}"
                )
                self.last_long_time = now

        if signal:
            self.last_signal_time = now
            self.signals_this_hour.append(now)

        return signal

    def record_result(self, is_win: bool, pnl: float):
        """记录信号结果"""
        self.results.append((is_win, pnl))
        if is_win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1

    @property
    def win_rate(self) -> float:
        if not self.results:
            return 0.0
        wins = sum(1 for r in self.results if r[0])
        return wins / len(self.results)

    @property
    def total_pnl(self) -> float:
        return sum(r[1] for r in self.results)

    @property
    def total_signals(self) -> int:
        return len(self.results)
