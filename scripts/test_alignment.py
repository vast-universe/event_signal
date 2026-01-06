#!/usr/bin/env python3
"""
测试 event_signal 与回测脚本的信号对齐
"""
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import pandas as pd
from datetime import datetime

from event_signal.config import (
    HORIZON, OVERBOUGHT, OVERSOLD, SIGNAL_THRESHOLDS, VOL_SPIKE_MAX
)
from event_signal.core import FeatureEngine, Kline, RiverModel

DATA_DIR = project_root / "data"
TRAIN_YEARS = [2024, 2025]
TEST_FILES = ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]
TEST_SYMBOLS = ['BTCUSDT', 'ETHUSDT']

FEATURE_COLS = [
    'rsi6', 'rsi14', 'bb_pct', 'vol_ratio',
    'ret5', 'ret10', 'ret20',
    'body_pct', 'upper_shadow', 'lower_shadow',
    'up_count', 'volatility'
]


def load_data(symbol: str, files: list):
    """加载数据"""
    dfs = []
    for f in files:
        filepath = DATA_DIR / f"{symbol}-1m-{f}.csv"
        if filepath.exists():
            df = pd.read_csv(filepath, header=None,
                           names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                  'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                  'taker_buy_quote', 'ignore'])
            if df['timestamp'].iloc[0] > 1e15:
                df['timestamp'] = df['timestamp'] // 1000
            dfs.append(df)
    
    if dfs:
        return pd.concat(dfs, ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')
    return None


def generate_signals_backtest_style(symbol: str):
    """回测脚本风格：边训练边预测"""
    train_files = [f"{y}-{m:02d}" for y in TRAIN_YEARS for m in range(1, 13)]
    train_data = load_data(symbol, train_files)
    test_data = load_data(symbol, TEST_FILES)
    
    if train_data is None or test_data is None:
        return None
    
    all_data = pd.concat([train_data, test_data], ignore_index=True)
    all_data = all_data.sort_values('timestamp').drop_duplicates('timestamp')
    all_data['future_price'] = all_data['close'].shift(-HORIZON)
    
    test_start_ts = test_data['timestamp'].min()
    
    model = RiverModel(horizon_minutes=HORIZON)
    engine = FeatureEngine(window_size=100)
    signals = []
    
    for _, row in all_data.iterrows():
        kline = Kline(int(row['timestamp']), float(row['open']), float(row['high']),
                      float(row['low']), float(row['close']), float(row['volume']))
        engine.add_kline(kline)
        
        if not engine.ready():
            continue
        
        feat = engine.compute()
        if feat is None:
            continue
        
        rsi6, bb_pct = feat['rsi6'], feat['bb_pct']
        vol_spike = feat.get('vol_spike', 1.0)
        price, future_price = row['close'], row['future_price']
        ts = row['timestamp']
        
        if pd.isna(future_price):
            continue
        
        x = {col: float(feat[col]) for col in FEATURE_COLS}
        is_test = ts >= test_start_ts
        
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            y = 1 if future_price < price else 0
            if is_test and vol_spike <= VOL_SPIKE_MAX:
                proba = model.model_down.predict_proba_one(x)
                if proba:
                    p = proba.get(True, proba.get(1, 0.5))
                    if p >= SIGNAL_THRESHOLDS['C']:
                        level = 'S' if p >= SIGNAL_THRESHOLDS['S'] else \
                                'A' if p >= SIGNAL_THRESHOLDS['A'] else \
                                'B' if p >= SIGNAL_THRESHOLDS['B'] else 'C'
                        signals.append({'timestamp': ts, 'direction': 'DOWN', 'level': level,
                                       'confidence': round(p, 4), 'is_win': future_price < price})
            model.model_down.learn_one(x, y)
        
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            y = 1 if future_price > price else 0
            if is_test and vol_spike <= VOL_SPIKE_MAX:
                proba = model.model_up.predict_proba_one(x)
                if proba:
                    p = proba.get(True, proba.get(1, 0.5))
                    if p >= SIGNAL_THRESHOLDS['C']:
                        level = 'S' if p >= SIGNAL_THRESHOLDS['S'] else \
                                'A' if p >= SIGNAL_THRESHOLDS['A'] else \
                                'B' if p >= SIGNAL_THRESHOLDS['B'] else 'C'
                        signals.append({'timestamp': ts, 'direction': 'UP', 'level': level,
                                       'confidence': round(p, 4), 'is_win': future_price > price})
            model.model_up.learn_one(x, y)
    
    return pd.DataFrame(signals)


def generate_signals_service_style(symbol: str):
    """event_signal 风格：先训练完，再在测试数据上边预测边学习"""
    train_files = [f"{y}-{m:02d}" for y in TRAIN_YEARS for m in range(1, 13)]
    train_data = load_data(symbol, train_files)
    test_data = load_data(symbol, TEST_FILES)
    
    if train_data is None or test_data is None:
        return None
    
    train_data = train_data.copy()
    train_data['future_price'] = train_data['close'].shift(-HORIZON)
    
    model = RiverModel(horizon_minutes=HORIZON)
    engine = FeatureEngine(window_size=100)
    
    # 训练阶段
    for _, row in train_data.iterrows():
        kline = Kline(int(row['timestamp']), float(row['open']), float(row['high']),
                      float(row['low']), float(row['close']), float(row['volume']))
        engine.add_kline(kline)
        
        if not engine.ready():
            continue
        
        feat = engine.compute()
        if feat is None:
            continue
        
        rsi6, bb_pct = feat['rsi6'], feat['bb_pct']
        price, future_price = row['close'], row['future_price']
        
        if pd.isna(future_price):
            continue
        
        x = {col: float(feat[col]) for col in FEATURE_COLS}
        
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            model.model_down.learn_one(x, 1 if future_price < price else 0)
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            model.model_up.learn_one(x, 1 if future_price > price else 0)
    
    # 测试阶段
    test_data = test_data.copy()
    test_data['future_price'] = test_data['close'].shift(-HORIZON)
    signals = []
    
    for _, row in test_data.iterrows():
        kline = Kline(int(row['timestamp']), float(row['open']), float(row['high']),
                      float(row['low']), float(row['close']), float(row['volume']))
        engine.add_kline(kline)
        
        if not engine.ready():
            continue
        
        feat = engine.compute()
        if feat is None:
            continue
        
        rsi6, bb_pct = feat['rsi6'], feat['bb_pct']
        vol_spike = feat.get('vol_spike', 1.0)
        price, future_price = row['close'], row['future_price']
        ts = row['timestamp']
        
        if pd.isna(future_price):
            continue
        
        x = {col: float(feat[col]) for col in FEATURE_COLS}
        
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            y = 1 if future_price < price else 0
            if vol_spike <= VOL_SPIKE_MAX:
                proba = model.model_down.predict_proba_one(x)
                if proba:
                    p = proba.get(True, proba.get(1, 0.5))
                    if p >= SIGNAL_THRESHOLDS['C']:
                        level = 'S' if p >= SIGNAL_THRESHOLDS['S'] else \
                                'A' if p >= SIGNAL_THRESHOLDS['A'] else \
                                'B' if p >= SIGNAL_THRESHOLDS['B'] else 'C'
                        signals.append({'timestamp': ts, 'direction': 'DOWN', 'level': level,
                                       'confidence': round(p, 4), 'is_win': future_price < price})
            model.model_down.learn_one(x, y)
        
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            y = 1 if future_price > price else 0
            if vol_spike <= VOL_SPIKE_MAX:
                proba = model.model_up.predict_proba_one(x)
                if proba:
                    p = proba.get(True, proba.get(1, 0.5))
                    if p >= SIGNAL_THRESHOLDS['C']:
                        level = 'S' if p >= SIGNAL_THRESHOLDS['S'] else \
                                'A' if p >= SIGNAL_THRESHOLDS['A'] else \
                                'B' if p >= SIGNAL_THRESHOLDS['B'] else 'C'
                        signals.append({'timestamp': ts, 'direction': 'UP', 'level': level,
                                       'confidence': round(p, 4), 'is_win': future_price > price})
            model.model_up.learn_one(x, y)
    
    return pd.DataFrame(signals)


def main():
    print("=" * 70)
    print("测试 event_signal 与回测脚本的信号对齐")
    print("=" * 70)
    
    for symbol in TEST_SYMBOLS:
        print(f"\n{'='*50}")
        print(f"{symbol}")
        print('='*50)
        
        print("\n生成回测脚本信号...")
        signals_bt = generate_signals_backtest_style(symbol)
        
        print("生成 event_signal 信号...")
        signals_svc = generate_signals_service_style(symbol)
        
        if signals_bt is None or signals_svc is None:
            print("  ⚠️ 数据加载失败")
            continue
        
        print(f"\n📊 信号数量: 回测 {len(signals_bt)}, service {len(signals_svc)}")
        
        if len(signals_bt) == 0 or len(signals_svc) == 0:
            continue
        
        ts_bt = set(signals_bt['timestamp'].tolist())
        ts_svc = set(signals_svc['timestamp'].tolist())
        overlap = ts_bt & ts_svc
        
        print(f"📊 重叠: {len(overlap)}, 仅回测: {len(ts_bt - ts_svc)}, 仅service: {len(ts_svc - ts_bt)}")
        
        if overlap:
            merged = signals_bt.merge(signals_svc, on='timestamp', suffixes=('_bt', '_svc'))
            dir_match = (merged['direction_bt'] == merged['direction_svc']).mean()
            level_match = (merged['level_bt'] == merged['level_svc']).mean()
            print(f"📊 方向一致: {dir_match*100:.1f}%, 等级一致: {level_match*100:.1f}%")
        
        print(f"📊 胜率: 回测 {signals_bt['is_win'].mean()*100:.1f}%, service {signals_svc['is_win'].mean()*100:.1f}%")


if __name__ == "__main__":
    main()
