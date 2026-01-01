# Event Signal

币安事件合约信号系统 - 基于 River 在线学习的量化交易信号服务

## 功能特点

- 🎯 实时监控 BTC/ETH 1分钟K线
- 📊 基于超买超卖 + River在线学习的信号生成
- 🔄 延迟标签机制，10分钟后自动学习和结算
- 💾 信号持久化存储 (PostgreSQL)
- 🌐 RESTful API + WebSocket 实时推送
- 📈 S/A/B/C 四级信号质量分级

## 回测结果

| 等级 | 置信度 | 胜率 | 日均信号 |
|------|--------|------|----------|
| S    | ≥75%   | ~81% | ~2笔     |
| A    | ≥70%   | ~75% | ~2笔     |
| B    | ≥65%   | ~70% | ~7笔     |
| C    | ≥60%   | ~63% | ~35笔    |

## 项目结构

```
event_signal/
├── src/event_signal/       # 源代码
│   ├── config.py           # 配置管理
│   ├── server.py           # FastAPI服务器
│   ├── core/               # 核心模块
│   │   ├── features.py     # 特征计算 (12个技术指标)
│   │   ├── model.py        # River模型 (LogisticRegression L2=0.5)
│   │   └── strategy.py     # 交易策略
│   ├── db/                 # 数据库
│   │   ├── database.py     # 连接管理
│   │   └── models.py       # 数据模型
│   ├── api/                # API接口
│   │   ├── routes.py       # 路由
│   │   ├── schemas.py      # 数据模型
│   │   └── websocket.py    # WebSocket推送
│   └── services/           # 业务服务
│       └── signal_service.py
├── scripts/                # 脚本
│   ├── run.py              # 运行服务
│   └── pretrain.py         # 预训练
├── models/                 # 模型文件
├── pyproject.toml          # 项目配置
├── .env.example            # 环境变量示例
└── README.md
```

## 安装

```bash
cd event_signal
pip install -r requirements.txt
```

## 配置

创建 `.env` 文件：

```bash
# PostgreSQL (Vercel/Neon)
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/database?ssl=require

# API配置
API_HOST=0.0.0.0
API_PORT=8000
```

注意：`DATABASE_URL` 必须使用 `postgresql+asyncpg://` 前缀，SSL参数用 `ssl=require`（不是 `sslmode`）

## 使用

### 1. 预训练模型

```bash
python scripts/pretrain.py
```

输出示例：
```
训练 BTCUSDT
✅ 加载 BTCUSDT: 480960 条K线

📈 按置信度分级统计:
  做空:
    S级:   249笔, 胜率 81.1% ✅
    A级:   324笔, 胜率 70.7% ✅
    B级:  1040笔, 胜率 68.9%
    C级:  4730笔, 胜率 63.7%
```

### 2. 运行服务

```bash
python scripts/run.py
```

### 3. API 接口

服务启动后访问:
- API文档: http://localhost:8000/docs
- 健康检查: GET /api/health
- 信号列表: GET /api/signals
- 最新信号: GET /api/signals/latest
- 统计数据: GET /api/stats
- 今日统计: GET /api/stats/today

### 4. WebSocket

连接 `ws://localhost:8000/ws` 接收实时推送：

```json
// 新信号
{"type": "signal", "data": {"id": 1, "symbol": "BTCUSDT", "direction": "DOWN", "level": "S", ...}}

// 结算结果
{"type": "settlement", "data": {"id": 1, "is_win": true, "pnl": 8.0, ...}}
```

## 策略说明

### 入场条件
- 超买做空: RSI6 ≥ 70 且 BB位置 ≥ 0.8
- 超卖做多: RSI6 ≤ 30 且 BB位置 ≤ 0.2

### 模型
- River LogisticRegression (L2=0.5)
- 在线学习：10分钟后根据实际涨跌更新模型

### 特征 (12个)
- RSI6, RSI14, BB_PCT
- VOL_RATIO, RET5/10/20
- BODY_PCT, UPPER/LOWER_SHADOW
- UP_COUNT, VOLATILITY

### 信号等级

| 等级 | 置信度 | 下注金额 |
|------|--------|----------|
| S    | ≥75%   | 10U      |
| A    | ≥70%   | 7U       |
| B    | ≥65%   | 5U       |
| C    | ≥60%   | 5U       |

## 数据库表结构

```sql
CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    direction VARCHAR(10) NOT NULL,  -- UP/DOWN
    level VARCHAR(5) NOT NULL,       -- S/A/B/C
    confidence FLOAT NOT NULL,
    entry_price FLOAT NOT NULL,
    bet_amount FLOAT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    settle_at TIMESTAMP,
    settle_price FLOAT,
    is_win BOOLEAN,
    pnl FLOAT,
    status VARCHAR(20) DEFAULT 'pending'  -- pending/settled
);
```

## License

MIT
