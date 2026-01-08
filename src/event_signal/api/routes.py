"""
API 路由
"""
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, HTTPException, Query

from ..config import (
    OVERBOUGHT, OVERSOLD, SIGNAL_THRESHOLDS, BET_AMOUNTS, 
    PAYOUT_RATE, VOL_SPIKE_MAX, HORIZON
)
from ..db import get_db
from ..db.models import SignalRepository
from .schemas import SignalResponse, SignalListResponse, StatsResponse, HealthResponse

router = APIRouter(prefix="/api", tags=["signals"])

_service_ref = None


def set_service_ref(service):
    """设置服务引用"""
    global _service_ref
    _service_ref = service


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查"""
    ws_connected = False
    pending = 0

    if _service_ref:
        ws_connected = _service_ref.ws is not None
        for handler in _service_ref.handlers.values():
            pending += len(handler.pending_signals)

    return HealthResponse(
        status="ok",
        service="event_signal",
        websocket_connected=ws_connected,
        pending_signals=pending
    )


@router.get("/klines/{symbol}")
async def get_klines(
    symbol: str,
    limit: int = Query(100, ge=1, le=100, description="返回K线数量，最大100"),
):
    """
    获取特征引擎中的K线数据，用于检查K线连续性
    """
    if not _service_ref:
        raise HTTPException(status_code=503, detail="Service not ready")
    
    handler = _service_ref.handlers.get(symbol)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    
    engine = handler.features
    klines = list(engine.klines)[-limit:]
    
    # 检查连续性
    gaps = []
    for i in range(1, len(klines)):
        expected_ts = klines[i-1].timestamp + 60000
        actual_ts = klines[i].timestamp
        if actual_ts != expected_ts:
            gap_minutes = (actual_ts - klines[i-1].timestamp) / 60000
            gaps.append({
                "index": i,
                "prev_time": datetime.utcfromtimestamp(klines[i-1].timestamp/1000).isoformat() + "Z",
                "curr_time": datetime.utcfromtimestamp(actual_ts/1000).isoformat() + "Z",
                "gap_minutes": gap_minutes
            })
    
    # 构建返回数据
    kline_data = []
    for k in klines:
        dt_utc = datetime.utcfromtimestamp(k.timestamp / 1000)
        dt_bj = dt_utc + timedelta(hours=8)
        kline_data.append({
            "timestamp": k.timestamp,
            "time_utc": dt_utc.isoformat() + "Z",
            "time_beijing": dt_bj.strftime("%Y-%m-%d %H:%M"),
            "open": k.open,
            "high": k.high,
            "low": k.low,
            "close": k.close,
            "volume": k.volume
        })
    
    return {
        "symbol": symbol,
        "total_klines": len(engine.klines),
        "returned": len(kline_data),
        "is_continuous": len(gaps) == 0,
        "gaps": gaps,
        "first_time": kline_data[0]["time_utc"] if kline_data else None,
        "last_time": kline_data[-1]["time_utc"] if kline_data else None,
        "klines": kline_data
    }


@router.get("/signals", response_model=SignalListResponse)
async def get_signals(
    symbol: Optional[str] = Query(None, description="交易对"),
    status: Optional[str] = Query(None, description="状态: pending/settled"),
    limit: int = Query(50, ge=1, le=200, description="数量限制"),
):
    """获取信号列表"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        signals = await repo.get_latest(limit)

        if symbol:
            signals = [s for s in signals if s.symbol == symbol]
        if status:
            signals = [s for s in signals if s.status == status]

        return SignalListResponse(
            signals=[SignalResponse.model_validate(s) for s in signals],
            total=len(signals)
        )


@router.get("/signals/latest", response_model=SignalListResponse)
async def get_latest_signals(limit: int = Query(10, ge=1, le=50)):
    """获取最新信号"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        signals = await repo.get_latest(limit)
        return SignalListResponse(
            signals=[SignalResponse.model_validate(s) for s in signals],
            total=len(signals)
        )


@router.get("/signals/{signal_id}", response_model=SignalResponse)
async def get_signal(signal_id: int):
    """获取单个信号"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        signal = await repo.get_by_id(signal_id)
        if not signal:
            raise HTTPException(status_code=404, detail="Signal not found")
        return SignalResponse.model_validate(signal)


@router.get("/stats", response_model=StatsResponse)
async def get_stats(days: Optional[int] = Query(None, description="统计天数")):
    """获取统计数据"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        stats = await repo.get_stats(days)
        return StatsResponse(**stats)


@router.get("/stats/today", response_model=StatsResponse)
async def get_today_stats():
    """获取今日统计"""
    db = await get_db()
    async with db.session() as session:
        repo = SignalRepository(session)
        stats = await repo.get_stats(today_only=True)
        return StatsResponse(**stats)


# ==================== 回测对比接口 ====================

FEATURE_COLS = [
    'rsi6', 'rsi14', 'bb_pct', 'vol_ratio',
    'ret5', 'ret10', 'ret20',
    'body_pct', 'upper_shadow', 'lower_shadow',
    'up_count', 'volatility'
]


def _get_level(prob: float) -> Optional[str]:
    """根据置信度返回信号等级"""
    for level, threshold in SIGNAL_THRESHOLDS.items():
        if prob >= threshold:
            return level
    return None


@router.get("/backtest/today")
async def backtest_today(
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，默认今天")
):
    """
    独立回测：从头预训练新模型，预测指定日期信号，与实盘对比
    
    流程：
    1. 创建全新的模型（不用实盘模型）
    2. 用历史数据预训练（到测试日前一天）
    3. 用测试日数据预测信号
    4. 与数据库中的实盘信号对比
    
    这样可以验证实盘的预训练逻辑是否正确
    """
    from river import linear_model, preprocessing
    from river.compose import Pipeline
    from ..core import FeatureEngine, Kline
    from ..config import MODEL_L2
    
    # 解析日期
    if date:
        try:
            target_date = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            raise HTTPException(status_code=400, detail="日期格式错误，应为 YYYY-MM-DD")
    else:
        target_date = datetime.utcnow()
    
    date_str = target_date.strftime('%Y-%m-%d')
    target_ts = int(target_date.replace(hour=0, minute=0, second=0).timestamp() * 1000)
    
    # 获取数据目录
    from ..server import DATA_DIR
    data_path = Path(DATA_DIR)
    
    backtest_signals = []
    model_info = {}
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        # ========== 1. 创建全新模型 ==========
        model_down = Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(l2=MODEL_L2),
        )
        model_up = Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(l2=MODEL_L2),
        )
        engine = FeatureEngine(window_size=100)
        
        # ========== 2. 加载并预训练 ==========
        train_data = _load_all_data(data_path, symbol)
        if train_data.empty:
            continue
        
        # 分割训练和测试数据
        train_df = train_data[train_data['timestamp'] < target_ts].copy()
        test_df = train_data[
            (train_data['timestamp'] >= target_ts) & 
            (train_data['timestamp'] < target_ts + 24*60*60*1000)
        ].copy()
        
        if train_df.empty:
            continue
        
        # 预训练（和实盘一样的逻辑）
        train_df['future_price'] = train_df['close'].shift(-HORIZON)
        trained_up, trained_down = 0, 0
        
        for _, row in train_df.iterrows():
            kline = Kline(
                timestamp=int(row['timestamp']),
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=float(row['volume'])
            )
            engine.add_kline(kline)
            
            if not engine.ready():
                continue
            
            feat = engine.compute()
            if feat is None:
                continue
            
            rsi6 = feat['rsi6']
            bb_pct = feat['bb_pct']
            price = row['close']
            future_price = row.get('future_price')
            
            if pd.isna(future_price):
                continue
            
            x = {col: float(feat[col]) for col in FEATURE_COLS}
            
            # 超买 → 做空训练
            if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
                y = 1 if future_price < price else 0
                model_down.learn_one(x, y)
                trained_down += 1
            
            # 超卖 → 做多训练
            elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
                y = 1 if future_price > price else 0
                model_up.learn_one(x, y)
                trained_up += 1
        
        model_info[symbol] = {
            'trained_down': trained_down,
            'trained_up': trained_up,
            'train_klines': len(train_df),
            'test_klines': len(test_df),
        }
        
        # ========== 3. 测试日预测 ==========
        if test_df.empty:
            continue
        
        test_df['future_price'] = test_df['close'].shift(-HORIZON)
        
        for _, row in test_df.iterrows():
            kline = Kline(
                timestamp=int(row['timestamp']),
                open=float(row['open']),
                high=float(row['high']),
                low=float(row['low']),
                close=float(row['close']),
                volume=float(row['volume'])
            )
            engine.add_kline(kline)
            
            if not engine.ready():
                continue
            
            feat = engine.compute()
            if feat is None:
                continue
            
            rsi6 = feat['rsi6']
            bb_pct = feat['bb_pct']
            vol_spike = feat.get('vol_spike', 1.0)
            price = row['close']
            future_price = row.get('future_price')
            
            x = {col: float(feat[col]) for col in FEATURE_COLS}
            ts = pd.to_datetime(row['timestamp'], unit='ms')
            ts_str = ts.strftime('%Y-%m-%d %H:%M')
            
            # 超买 → 做空
            if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
                if vol_spike <= VOL_SPIKE_MAX:
                    proba = model_down.predict_proba_one(x)
                    if proba:
                        p = proba.get(True, proba.get(1, 0.5))
                        level = _get_level(p)
                        if level:
                            is_win = None
                            pnl = None
                            if not pd.isna(future_price):
                                is_win = future_price < price
                                pnl = BET_AMOUNTS[level] * PAYOUT_RATE if is_win else -BET_AMOUNTS[level]
                            
                            backtest_signals.append({
                                'timestamp': ts_str,
                                'symbol': symbol,
                                'direction': 'DOWN',
                                'level': level,
                                'confidence': round(p, 4),
                                'entry_price': price,
                                'settle_price': future_price if not pd.isna(future_price) else None,
                                'is_win': is_win,
                                'pnl': pnl,
                                'rsi6': round(rsi6, 1),
                                'bb_pct': round(bb_pct, 3),
                                'vol_spike': round(vol_spike, 2),
                            })
            
            # 超卖 → 做多
            elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
                if vol_spike <= VOL_SPIKE_MAX:
                    proba = model_up.predict_proba_one(x)
                    if proba:
                        p = proba.get(True, proba.get(1, 0.5))
                        level = _get_level(p)
                        if level:
                            is_win = None
                            pnl = None
                            if not pd.isna(future_price):
                                is_win = future_price > price
                                pnl = BET_AMOUNTS[level] * PAYOUT_RATE if is_win else -BET_AMOUNTS[level]
                            
                            backtest_signals.append({
                                'timestamp': ts_str,
                                'symbol': symbol,
                                'direction': 'UP',
                                'level': level,
                                'confidence': round(p, 4),
                                'entry_price': price,
                                'settle_price': future_price if not pd.isna(future_price) else None,
                                'is_win': is_win,
                                'pnl': pnl,
                                'rsi6': round(rsi6, 1),
                                'bb_pct': round(bb_pct, 3),
                                'vol_spike': round(vol_spike, 2),
                            })
    
    # ========== 4. 从数据库获取实盘信号 ==========
    db = await get_db()
    live_signals = []
    async with db.session() as session:
        repo = SignalRepository(session)
        all_signals = await repo.get_latest(500)
        
        for sig in all_signals:
            sig_date = sig.created_at.strftime('%Y-%m-%d')
            if sig_date == date_str:
                live_signals.append({
                    'id': sig.id,
                    'timestamp': sig.created_at.strftime('%Y-%m-%d %H:%M'),
                    'symbol': sig.symbol,
                    'direction': sig.direction,
                    'level': sig.level,
                    'confidence': round(sig.confidence, 4),
                    'entry_price': sig.entry_price,
                    'settle_price': sig.settle_price,
                    'is_win': sig.is_win,
                    'pnl': sig.pnl,
                    'status': sig.status,
                })
    
    # ========== 5. 对比分析 ==========
    bt_set = set((s['timestamp'], s['symbol'], s['direction']) for s in backtest_signals)
    live_set = set((s['timestamp'], s['symbol'], s['direction']) for s in live_signals)
    
    common = bt_set & live_set
    only_backtest = bt_set - live_set
    only_live = live_set - bt_set
    
    # 统计
    bt_wins = sum(1 for s in backtest_signals if s.get('is_win') is True)
    bt_total = sum(1 for s in backtest_signals if s.get('is_win') is not None)
    bt_pnl = sum(s.get('pnl', 0) or 0 for s in backtest_signals)
    
    live_wins = sum(1 for s in live_signals if s.get('is_win') is True)
    live_total = sum(1 for s in live_signals if s.get('is_win') is not None)
    live_pnl = sum(s.get('pnl', 0) or 0 for s in live_signals)
    
    return {
        'date': date_str,
        'mode': 'independent_backtest',  # 标记是独立回测
        'model_info': model_info,
        'backtest': {
            'signals': backtest_signals,
            'count': len(backtest_signals),
            'win_rate': bt_wins / bt_total if bt_total > 0 else None,
            'pnl': bt_pnl,
        },
        'live': {
            'signals': live_signals,
            'count': len(live_signals),
            'win_rate': live_wins / live_total if live_total > 0 else None,
            'pnl': live_pnl,
        },
        'comparison': {
            'common': len(common),
            'only_backtest': len(only_backtest),
            'only_live': len(only_live),
            'match_rate': len(common) / max(len(bt_set), len(live_set), 1),
        },
        'only_backtest_signals': [
            s for s in backtest_signals 
            if (s['timestamp'], s['symbol'], s['direction']) in only_backtest
        ][:10],
        'only_live_signals': [
            s for s in live_signals 
            if (s['timestamp'], s['symbol'], s['direction']) in only_live
        ][:10],
    }


def _load_all_data(data_path: Path, symbol: str) -> pd.DataFrame:
    """加载所有数据（2024-2025 + 2026日度文件）"""
    dfs = []
    
    # 加载2024-2025年数据
    for year in [2024, 2025]:
        for month in range(1, 13):
            fp = data_path / f'{symbol}-1m-{year}-{month:02d}.csv'
            if fp.exists():
                df = pd.read_csv(fp, header=None,
                               names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                      'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                      'taker_buy_quote', 'ignore'])
                if df['timestamp'].iloc[0] > 1e15:
                    df['timestamp'] = df['timestamp'] // 1000
                dfs.append(df)
    
    # 加载日度文件（2026年）
    for day in range(1, 32):
        fp = data_path / f'{symbol}-1m-2026-01-{day:02d}.csv'
        if fp.exists():
            df = pd.read_csv(fp, header=None,
                           names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                  'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                  'taker_buy_quote', 'ignore'])
            if df['timestamp'].iloc[0] > 1e15:
                df['timestamp'] = df['timestamp'] // 1000
            dfs.append(df)
    
    if not dfs:
        return pd.DataFrame()
    
    return pd.concat(dfs, ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')


def _load_all_data(data_path: Path, symbol: str) -> pd.DataFrame:
    """加载所有数据（2024-2025 + 2026日度文件）"""
    dfs = []
    
    # 加载2024-2025年数据
    for year in [2024, 2025]:
        for month in range(1, 13):
            fp = data_path / f'{symbol}-1m-{year}-{month:02d}.csv'
            if fp.exists():
                df = pd.read_csv(fp, header=None,
                               names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                      'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                      'taker_buy_quote', 'ignore'])
                if df['timestamp'].iloc[0] > 1e15:
                    df['timestamp'] = df['timestamp'] // 1000
                dfs.append(df)
    
    # 加载日度文件（2026年）
    for day in range(1, 32):
        fp = data_path / f'{symbol}-1m-2026-01-{day:02d}.csv'
        if fp.exists():
            df = pd.read_csv(fp, header=None,
                           names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                  'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                  'taker_buy_quote', 'ignore'])
            if df['timestamp'].iloc[0] > 1e15:
                df['timestamp'] = df['timestamp'] // 1000
            dfs.append(df)
    
    if not dfs:
        return pd.DataFrame()
    
    return pd.concat(dfs, ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')


@router.get("/model/predict")
async def model_predict(
    symbol: str = Query(..., description="交易对 BTCUSDT/ETHUSDT"),
):
    """
    获取当前模型对最新K线的预测
    用于验证模型状态是否正常
    """
    if not _service_ref:
        raise HTTPException(status_code=503, detail="Service not ready")
    
    handler = _service_ref.handlers.get(symbol)
    if not handler:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found")
    
    feat = handler.last_features
    if not feat:
        return {"error": "No features available yet"}
    
    x = {col: float(feat.get(col, 0)) for col in FEATURE_COLS}
    
    # 预测
    prob_down = handler.model.predict_down(feat)
    prob_up = handler.model.predict_up(feat)
    
    # 获取模型内部预测
    proba_down = handler.model.model_down.predict_proba_one(x)
    proba_up = handler.model.model_up.predict_proba_one(x)
    
    return {
        'symbol': symbol,
        'features': {
            'rsi6': feat.get('rsi6'),
            'rsi14': feat.get('rsi14'),
            'bb_pct': feat.get('bb_pct'),
            'vol_ratio': feat.get('vol_ratio'),
            'vol_spike': feat.get('vol_spike'),
        },
        'predictions': {
            'prob_down': prob_down,
            'prob_up': prob_up,
            'raw_proba_down': proba_down,
            'raw_proba_up': proba_up,
        },
        'model_stats': {
            'down_samples': handler.model.stats['down']['total'],
            'down_correct': handler.model.stats['down']['correct'],
            'down_accuracy': handler.model.accuracy_down,
            'up_samples': handler.model.stats['up']['total'],
            'up_correct': handler.model.stats['up']['correct'],
            'up_accuracy': handler.model.accuracy_up,
        },
        'thresholds': SIGNAL_THRESHOLDS,
        'would_signal': {
            'down': prob_down >= 0.6 and feat.get('rsi6', 50) >= 60 and feat.get('bb_pct', 0.5) >= 0.7,
            'up': prob_up >= 0.6 and feat.get('rsi6', 50) <= 40 and feat.get('bb_pct', 0.5) <= 0.3,
        }
    }
