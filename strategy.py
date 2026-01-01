"""
交易策略 - 基于超买超卖 + River在线学习
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .config import (
    OVERBOUGHT, OVERSOLD, 
    SIGNAL_THRESHOLDS, BET_AMOUNTS,
    MAX_SIGNALS_PER_HOUR, MAX_CONSECUTIVE_LOSS, COOLDOWN_MINUTES,
    get_signal_level, get_bet_amount
)


@dataclass
class Signal:
    """交易信号"""
    symbol: str
    direction: str      # "UP" 或 "DOWN"
    level: str          # "S", "A", "B", "C"
    price: float
    confidence: float   # 置信度
    bet_amount: float   # 下注金额
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
        self.is_paused = False
        self.pause_until: Optional[datetime] = None
        
        # 信号结果记录
        self.results: list = []
    
    def check(self, price: float, features: dict, 
              prob_down: float, prob_up: float) -> Optional[Signal]:
        """检查是否产生信号"""
        now = datetime.now()
        
        # 风控检查
        if not self._risk_check(now):
            return None
        
        if not features:
            return None
        
        rsi6 = features.get('rsi6', 50)
        bb_pct = features.get('bb_pct', 0.5)
        
        signal = None
        
        # 1. 超买区域 → 做空信号
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            level = get_signal_level(prob_down)
            if level:
                signal = Signal(
                    symbol=self.symbol,
                    direction="DOWN",
                    level=level,
                    price=price,
                    confidence=prob_down,
                    bet_amount=get_bet_amount(level),
                    timestamp=now,
                    details=f"RSI6={rsi6:.0f} BB={bb_pct:.2f} P(跌)={prob_down:.1%}"
                )
        
        # 2. 超卖区域 → 做多信号
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            level = get_signal_level(prob_up)
            if level:
                signal = Signal(
                    symbol=self.symbol,
                    direction="UP",
                    level=level,
                    price=price,
                    confidence=prob_up,
                    bet_amount=get_bet_amount(level),
                    timestamp=now,
                    details=f"RSI6={rsi6:.0f} BB={bb_pct:.2f} P(涨)={prob_up:.1%}"
                )
        
        if signal:
            self.last_signal_time = now
            self.signals_this_hour.append(now)
        
        return signal

    def _risk_check(self, now: datetime) -> bool:
        """风控检查"""
        # 暂停检查
        if self.is_paused:
            if self.pause_until and now >= self.pause_until:
                self.is_paused = False
                self.consecutive_losses = 0
                print(f"⏰ {self.symbol} 冷却结束，恢复交易")
            else:
                return False
        
        # 每小时信号数限制
        one_hour_ago = now.timestamp() - 3600
        self.signals_this_hour = [
            t for t in self.signals_this_hour 
            if t.timestamp() > one_hour_ago
        ]
        if len(self.signals_this_hour) >= MAX_SIGNALS_PER_HOUR:
            return False
        
        return True
    
    def record_result(self, is_win: bool, pnl: float):
        """记录信号结果"""
        self.results.append((is_win, pnl))
        
        if is_win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            
            # 连续亏损暂停
            if self.consecutive_losses >= MAX_CONSECUTIVE_LOSS:
                self.is_paused = True
                self.pause_until = datetime.fromtimestamp(
                    datetime.now().timestamp() + COOLDOWN_MINUTES * 60
                )
                print(f"⚠️ {self.symbol} 连续亏损{self.consecutive_losses}次，暂停{COOLDOWN_MINUTES}分钟")
    
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
    
    def get_stats_by_level(self) -> dict:
        """按等级统计"""
        # 需要在record_result时记录level
        return {}
