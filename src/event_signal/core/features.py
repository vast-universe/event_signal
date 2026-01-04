"""
特征计算器 - 从1分钟K线计算技术指标
"""
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Kline:
    """K线数据"""

    timestamp: int  # 毫秒时间戳
    open: float
    high: float
    low: float
    close: float
    volume: float


class FeatureEngine:
    """特征引擎 - 增量计算技术指标"""

    def __init__(self, window_size: int = 100):
        self.klines: deque[Kline] = deque(maxlen=window_size)
        self._cache = {}

    def add_kline(self, kline: Kline):
        """添加K线并清除缓存"""
        self.klines.append(kline)
        self._cache.clear()

    def ready(self) -> bool:
        """是否有足够数据"""
        return len(self.klines) >= 30

    @property
    def closes(self) -> np.ndarray:
        if "closes" not in self._cache:
            self._cache["closes"] = np.array([k.close for k in self.klines])
        return self._cache["closes"]

    @property
    def volumes(self) -> np.ndarray:
        if "volumes" not in self._cache:
            self._cache["volumes"] = np.array([k.volume for k in self.klines])
        return self._cache["volumes"]

    @property
    def highs(self) -> np.ndarray:
        if "highs" not in self._cache:
            self._cache["highs"] = np.array([k.high for k in self.klines])
        return self._cache["highs"]

    @property
    def lows(self) -> np.ndarray:
        if "lows" not in self._cache:
            self._cache["lows"] = np.array([k.low for k in self.klines])
        return self._cache["lows"]

    @property
    def opens(self) -> np.ndarray:
        if "opens" not in self._cache:
            self._cache["opens"] = np.array([k.open for k in self.klines])
        return self._cache["opens"]

    def compute(self) -> Optional[dict]:
        """计算全部特征（12个核心特征）"""
        if not self.ready():
            return None

        c, v, h, l, o = self.closes, self.volumes, self.highs, self.lows, self.opens
        feat = {}

        # 1. RSI6 - 短期超买超卖
        feat["rsi6"] = self._calc_rsi(c, 6)

        # 2. RSI14 - 中期趋势
        feat["rsi14"] = self._calc_rsi(c, 14)

        # 3. 布林带位置 (0-1)
        feat["bb_pct"] = self._calc_bb_position(c, 20)

        # 4. 成交量比率
        vol_ma = np.mean(v[-20:]) if len(v) >= 20 else np.mean(v)
        feat["vol_ratio"] = v[-1] / vol_ma if vol_ma > 0 else 1.0

        # 5-7. 收益率
        for w in [5, 10, 20]:
            if len(c) > w:
                feat[f"ret{w}"] = (c[-1] / c[-w - 1] - 1) * 100
            else:
                feat[f"ret{w}"] = 0.0

        # 8. K线实体百分比
        feat["body_pct"] = (c[-1] - o[-1]) / o[-1] * 100

        # 9. 上影线
        feat["upper_shadow"] = (h[-1] - max(c[-1], o[-1])) / c[-1] * 100

        # 10. 下影线
        feat["lower_shadow"] = (min(c[-1], o[-1]) - l[-1]) / c[-1] * 100

        # 11. 最近5根上涨数量
        if len(c) >= 5:
            feat["up_count"] = sum(1 for i in range(-4, 0) if c[i] > c[i - 1])
        else:
            feat["up_count"] = 2

        # 12. 波动率
        if len(c) >= 20:
            feat["volatility"] = np.std(c[-20:]) / np.mean(c[-20:]) * 100
        else:
            feat["volatility"] = 0.0

        # 13. 成交量突变（当前/上一根）
        if len(v) >= 2 and v[-2] > 0:
            feat["vol_spike"] = v[-1] / v[-2]
        else:
            feat["vol_spike"] = 1.0

        return feat

    def _calc_rsi(self, closes: np.ndarray, period: int) -> float:
        """计算RSI"""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes[-(period + 1) :])
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _calc_bb_position(self, closes: np.ndarray, period: int = 20) -> float:
        """计算布林带位置 (0-1)"""
        if len(closes) < period:
            return 0.5

        mid = np.mean(closes[-period:])
        std = np.std(closes[-period:])

        if std == 0:
            return 0.5

        upper = mid + 2 * std
        lower = mid - 2 * std

        pos = (closes[-1] - lower) / (upper - lower)
        return max(0, min(1, pos))

    @property
    def last_close(self) -> float:
        return self.klines[-1].close if self.klines else 0.0

    @property
    def last_timestamp(self) -> int:
        return self.klines[-1].timestamp if self.klines else 0
