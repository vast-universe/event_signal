#!/usr/bin/env python3
"""
回测脚本 - 与 event_signal 实盘代码完全一致
"""
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

from event_signal.config import (
    HORIZON, VOL_SPIKE_MAX, PAYOUT_RATE, OVERBOUGHT, OVERSOLD,
    SIGNAL_THRESHOLDS, BET_AMOUNTS
)
from event_signal.core import FeatureEngine, Kline, RiverModel

DATA_DIR = project_root / "data"

FEATURE_COLS = [
    'rsi6', 'rsi14', 'bb_pct', 'vol_ratio',
    'ret5', 'ret10', 'ret20',
    'body_pct', 'upper_shadow', 'lower_shadow',
    'up_count', 'volatility'
]


def get_level(prob):
    for level, threshold in SIGNAL_THRESHOLDS.items():
        if prob >= threshold:
            return level
    return None


def load_klines(years=None):
    """加载所有K线数据"""
    if years is None:
        years = [2024, 2025]
    all_klines = {}
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        dfs = []
        for year in years:
            for month in range(1, 13):
                filepath = DATA_DIR / f'{symbol}-1m-{year}-{month:02d}.csv'
                if filepath.exists():
                    df = pd.read_csv(filepath, header=None,
                                   names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                          'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                          'taker_buy_quote', 'ignore'])
                    if df['timestamp'].iloc[0] > 1e15:
                        df['timestamp'] = df['timestamp'] // 1000
                    dfs.append(df)
        if dfs:
            data = pd.concat(dfs, ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')
            all_klines[symbol] = data
    return all_klines


def generate_signals(all_klines, warmup=1000):
    """生成信号 - 模拟实盘流程"""
    signals = []
    skipped_vol = 0
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"  处理 {symbol}...")
        df = all_klines[symbol]
        df['future_price'] = df['close'].shift(-HORIZON)
        
        engine = FeatureEngine(window_size=100)
        model = RiverModel(horizon_minutes=HORIZON)
        
        trained_up = 0
        trained_down = 0
        
        for i, row in df.iterrows():
            kline = Kline(
                timestamp=int(row['timestamp']),
                open=row['open'],
                high=row['high'],
                low=row['low'],
                close=row['close'],
                volume=row['volume']
            )
            engine.add_kline(kline)
            
            if not engine.ready():
                continue
            
            feat = engine.compute()
            if feat is None:
                continue
            
            rsi6 = feat['rsi6']
            bb_pct = feat['bb_pct']
            vol_spike = feat['vol_spike']
            price = row['close']
            future_price = row['future_price']
            
            if pd.isna(future_price):
                continue
            
            x = {col: float(feat[col]) for col in FEATURE_COLS}
            
            # 超买 → 做空
            if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
                y = 1 if future_price < price else 0
                
                if trained_down >= warmup:
                    if vol_spike > VOL_SPIKE_MAX:
                        skipped_vol += 1
                    else:
                        proba = model.model_down.predict_proba_one(x)
                        if proba:
                            p = proba.get(True, proba.get(1, 0.5))
                            level = get_level(p)
                            if level:
                                bet = BET_AMOUNTS[level]
                                is_win = y == 1
                                pnl = bet * PAYOUT_RATE if is_win else -bet
                                
                                dt = pd.to_datetime(row['timestamp'], unit='ms')
                                signals.append({
                                    'timestamp': dt.strftime('%Y-%m-%d %H:%M:%S'),
                                    'symbol': symbol,
                                    'direction': 'SHORT',
                                    'level': level,
                                    'confidence': p,
                                    'entry_price': price,
                                    'settle_price': future_price,
                                    'bet_amount': bet,
                                    'is_win': is_win,
                                    'pnl': pnl,
                                })
                
                model.model_down.learn_one(x, y)
                trained_down += 1
            
            # 超卖 → 做多
            elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
                y = 1 if future_price > price else 0
                
                if trained_up >= warmup:
                    if vol_spike > VOL_SPIKE_MAX:
                        skipped_vol += 1
                    else:
                        proba = model.model_up.predict_proba_one(x)
                        if proba:
                            p = proba.get(True, proba.get(1, 0.5))
                            level = get_level(p)
                            if level:
                                bet = BET_AMOUNTS[level]
                                is_win = y == 1
                                pnl = bet * PAYOUT_RATE if is_win else -bet
                                
                                dt = pd.to_datetime(row['timestamp'], unit='ms')
                                signals.append({
                                    'timestamp': dt.strftime('%Y-%m-%d %H:%M:%S'),
                                    'symbol': symbol,
                                    'direction': 'LONG',
                                    'level': level,
                                    'confidence': p,
                                    'entry_price': price,
                                    'settle_price': future_price,
                                    'bet_amount': bet,
                                    'is_win': is_win,
                                    'pnl': pnl,
                                })
                
                model.model_up.learn_one(x, y)
                trained_up += 1
        
        print(f"    做多训练: {trained_up} 样本, 做空训练: {trained_down} 样本")
    
    print(f"  vol_spike > {VOL_SPIKE_MAX} 跳过: {skipped_vol} 笔")
    return pd.DataFrame(signals).sort_values('timestamp').reset_index(drop=True)


def main():
    print("=" * 60)
    print("回测脚本 - 与实盘代码一致")
    print("=" * 60)
    
    years = [2024, 2025]
    print(f"\n加载K线数据 ({years[0]}-{years[-1]})...")
    all_klines = load_klines(years=years)
    
    print("\n生成信号...")
    df = generate_signals(all_klines, warmup=1000)
    
    days = (pd.to_datetime(df['timestamp']).max() - pd.to_datetime(df['timestamp']).min()).days + 1
    
    print(f"\n{'='*60}")
    print("信号统计")
    print(f"{'='*60}")
    print(f"  总信号: {len(df)} 笔 ({len(df)/days:.1f}/天)")
    print(f"  总胜率: {df['is_win'].mean()*100:.1f}%")
    print(f"  总盈亏: {df['pnl'].sum():+.0f}U ({df['pnl'].sum()/days:+.1f}/天)")
    
    print(f"\n  按等级:")
    for level in ['S', 'A', 'B', 'C']:
        ldf = df[df['level'] == level]
        if len(ldf) > 0:
            wr = ldf['is_win'].mean() * 100
            pnl = ldf['pnl'].sum()
            print(f"    {level}级: {len(ldf):>5}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+8.0f}U")
    
    output_file = project_root / "signals_backtest.csv"
    df['cumulative_pnl'] = df['pnl'].cumsum()
    df.to_csv(output_file, index=False)
    print(f"\n✅ 已保存到: {output_file}")


if __name__ == '__main__':
    main()
