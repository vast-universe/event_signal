"""
Event Signal 配置
目标：预测10分钟后价格涨跌，胜率 > 55.56% 才能盈利
"""

# ========== 交易规则 ==========
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
HORIZON = 10  # 预测周期：10分钟
PAYOUT_RATE = 0.80  # 收益率 80%
BREAKEVEN_WINRATE = 1 / (1 + PAYOUT_RATE)  # 55.56%

# ========== 信号触发条件 ==========
# 超买区域 → 做空
OVERBOUGHT = {
    "rsi6_min": 70,
    "bb_pct_min": 0.8,
}

# 超卖区域 → 做多
OVERSOLD = {
    "rsi6_max": 30,
    "bb_pct_max": 0.2,
}

# ========== 信号等级阈值 ==========
SIGNAL_THRESHOLDS = {
    "S": 0.75,  # 极高置信度
    "A": 0.70,  # 高置信度
    "B": 0.65,  # 中等置信度
    "C": 0.60,  # 普通置信度
}

# ========== 下注金额 ==========
BET_AMOUNTS = {
    "S": 10,  # S级下10U
    "A": 7,   # A级下7U
    "B": 5,   # B级下5U
    "C": 5,   # C级下5U
}

# ========== 风控配置 ==========
MAX_DAILY_LOSS = 100          # 日最大亏损 100U
MAX_CONSECUTIVE_LOSS = 5      # 连续亏损5次暂停
COOLDOWN_MINUTES = 30         # 暂停后冷却30分钟
MAX_SIGNALS_PER_HOUR = 20     # 每小时最大信号数

# ========== 模型配置 ==========
MODEL_SAVE_INTERVAL = 10      # 模型保存间隔（分钟）
WARMUP_SAMPLES = 1000         # 模型预热样本数


def get_signal_level(proba: float) -> str:
    """根据置信度返回信号等级"""
    if proba >= SIGNAL_THRESHOLDS["S"]:
        return "S"
    elif proba >= SIGNAL_THRESHOLDS["A"]:
        return "A"
    elif proba >= SIGNAL_THRESHOLDS["B"]:
        return "B"
    elif proba >= SIGNAL_THRESHOLDS["C"]:
        return "C"
    return None


def get_bet_amount(level: str) -> float:
    """根据信号等级返回下单金额"""
    return BET_AMOUNTS.get(level, 5)
