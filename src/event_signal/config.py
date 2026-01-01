"""
配置管理
"""
import os
from pathlib import Path
from typing import Optional

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    load_dotenv(env_path)
except ImportError:
    pass

# ========== 交易配置 ==========
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
HORIZON = 10  # 预测周期(分钟)
PAYOUT_RATE = 0.80  # 收益率
BREAKEVEN_WINRATE = 1 / (1 + PAYOUT_RATE)  # 盈亏平衡胜率 55.56%

# ========== 入场条件 ==========
OVERBOUGHT = {"rsi6_min": 70, "bb_pct_min": 0.8}  # 超买做空
OVERSOLD = {"rsi6_max": 30, "bb_pct_max": 0.2}  # 超卖做多

# ========== 信号阈值 ==========
SIGNAL_THRESHOLDS = {"S": 0.75, "A": 0.70, "B": 0.65, "C": 0.60}

# ========== 下注金额 ==========
BET_AMOUNTS = {"S": 10, "A": 7, "B": 5, "C": 5}

# ========== 模型配置 ==========
MODEL_L2 = 0.5  # L2正则化参数
MODEL_SAVE_INTERVAL = 10  # 模型保存间隔(分钟)

# ========== 数据库配置 ==========
DATABASE_URL = os.getenv("DATABASE_URL")  # 运行服务时必填

# ========== API配置 ==========
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8000"))


def get_signal_level(confidence: float) -> Optional[str]:
    """根据置信度返回信号等级"""
    for level, threshold in SIGNAL_THRESHOLDS.items():
        if confidence >= threshold:
            return level
    return None


def get_bet_amount(level: str) -> float:
    """根据信号等级返回下注金额"""
    return BET_AMOUNTS.get(level, 5)
