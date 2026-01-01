# Event Signal - 币安事件合约信号系统

基于 River 在线学习的实时交易信号生成系统，用于币安事件合约（10分钟周期）。

## 策略概述

- **入场条件**: 超买做空 (RSI6>70, BB>0.8) + 超卖做多 (RSI6<30, BB<0.2)
- **模型**: LogisticRegression (L2=0.5) + StandardScaler
- **在线学习**: 每笔交易结算后自动更新模型

## 信号等级

| 等级 | 置信度 | 胜率 | 下注金额 |
|-----|-------|------|---------|
| S级 | ≥75% | ~79% | 10U |
| A级 | ≥70% | ~74% | 7U |
| B级 | ≥65% | ~70% | 5U |
| C级 | ≥60% | ~63% | 5U |

## 回测表现 (10-11月)

- 日均交易: 75笔
- 日均盈亏: +80.7 USDT
- 总体胜率: 65.7%

## 快速开始

```bash
# 1. 安装依赖
pip install -r event_signal/requirements.txt

# 2. 预训练模型 (使用历史数据)
python event_signal/pretrain.py

# 3. 运行实时信号
python event_signal/run.py
```

## 文件结构

```
event_signal/
├── __init__.py      # 包初始化
├── config.py        # 配置参数
├── features.py      # 特征计算
├── model.py         # River在线学习模型
├── strategy.py      # 交易策略
├── service.py       # WebSocket服务
├── pretrain.py      # 预训练脚本
├── run.py           # 启动入口
├── models/          # 保存的模型文件
│   ├── BTCUSDT.pkl
│   └── ETHUSDT.pkl
└── README.md
```

## 特征列表

| 特征 | 说明 |
|-----|------|
| rsi6, rsi14 | RSI指标 |
| bb_pct | 布林带位置 (0-1) |
| vol_ratio | 成交量比率 |
| ret5, ret10, ret20 | 收益率 |
| body_pct | K线实体比例 |
| upper_shadow | 上影线 |
| lower_shadow | 下影线 |
| up_count | 连续上涨次数 |
| volatility | 波动率 |

## 配置说明

`config.py` 主要参数:

```python
HORIZON = 10              # 预测周期(分钟)
PAYOUT_RATE = 0.80        # 收益率
BREAKEVEN_WINRATE = 0.556 # 盈亏平衡胜率

# 入场条件
OVERBOUGHT = {'rsi6_min': 70, 'bb_pct_min': 0.8}  # 超买做空
OVERSOLD = {'rsi6_max': 30, 'bb_pct_max': 0.2}    # 超卖做多

# 信号阈值
SIGNAL_THRESHOLDS = {'S': 0.75, 'A': 0.70, 'B': 0.65, 'C': 0.60}

# 下注金额
BET_AMOUNTS = {'S': 10, 'A': 7, 'B': 5, 'C': 5}
```

## 模型保存

- 每10分钟自动保存模型
- 模型文件: `event_signal/models/{SYMBOL}.pkl`
- 重启后自动加载已保存的模型

## 注意事项

1. 需要稳定的网络连接 Binance WebSocket
2. 信号仅供参考，用户自行决定是否下单
3. 模型会持续在线学习，适应市场变化
# event_signal
