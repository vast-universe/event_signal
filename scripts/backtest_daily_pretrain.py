#!/usr/bin/env python3
"""
回测脚本 - 每日预训练
每天用前一天的数据更新模型，然后预测当天（不学习）
"""
import sys
from pathlib import Path

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

DATA_DIR = Path(__file__).parent.parent.parent / "data"

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


def load_klines_monthly(symbol, years):
    """加载月度K线数据"""
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
        return pd.concat(dfs, ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')
    return pd.DataFrame()


def load_klines_daily(symbol, year, month, day):
    """加载单日K线数据"""
    filepath = DATA_DIR / f'{symbol}-1m-{year}-{month:02d}-{day:02d}.csv'
    if filepath.exists():
        df = pd.read_csv(filepath, header=None,
                       names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                              'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                              'taker_buy_quote', 'ignore'])
        if df['timestamp'].iloc[0] > 1e15:
            df['timestamp'] = df['timestamp'] // 1000
        return df.sort_values('timestamp').drop_duplicates('timestamp')
    return pd.DataFrame()


def train_on_data(engine, model, df):
    """在数据上训练模型"""
    df = df.copy()
    df['future_price'] = df['close'].shift(-HORIZON)
    
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
        price = row['close']
        future_price = row['future_price']
        
        if pd.isna(future_price):
            continue
        
        x = {col: float(feat[col]) for col in FEATURE_COLS}
        
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            y = 1 if future_price < price else 0
            model.model_down.learn_one(x, y)
            trained_down += 1
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            y = 1 if future_price > price else 0
            model.model_up.learn_one(x, y)
            trained_up += 1
    
    return trained_up, trained_down


def test_no_learn(engine, model, df, symbol):
    """测试（不学习）"""
    df = df.copy()
    df['future_price'] = df['close'].shift(-HORIZON)
    
    signals = []
    skipped_vol = 0
    
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
        
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            y = 1 if future_price < price else 0
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
        
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            y = 1 if future_price > price else 0
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
    
    return signals, skipped_vol


def main():
    print("=" * 60)
    print("回测脚本 - 每日预训练")
    print("每天用前一天数据更新模型，然后预测当天")
    print("=" * 60)
    
    train_years = [2024, 2025]
    test_year = 2026
    test_month = 1
    test_days = list(range(1, 9))  # 1-8号
    
    all_signals = []
    total_skipped = 0
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n{'='*60}")
        print(f"处理 {symbol}")
        print(f"{'='*60}")
        
        # 1. 加载历史数据预训练
        print(f"\n[1] 加载历史数据 ({train_years[0]}-{train_years[-1]})...")
        train_df = load_klines_monthly(symbol, train_years)
        print(f"    历史数据: {len(train_df):,} 条K线")
        
        # 2. 初始预训练
        print(f"\n[2] 初始预训练...")
        engine = FeatureEngine(window_size=100)
        model = RiverModel(horizon_minutes=HORIZON)
        trained_up, trained_down = train_on_data(engine, model, train_df)
        print(f"    做多训练: {trained_up:,} 样本")
        print(f"    做空训练: {trained_down:,} 样本")
        
        # 3. 每日测试
        print(f"\n[3] 每日测试...")
        prev_day_df = None
        
        for day in test_days:
            # 加载当天数据
            day_df = load_klines_daily(symbol, test_year, test_month, day)
            if len(day_df) == 0:
                print(f"    {test_month:02d}-{day:02d}: 无数据")
                continue
            
            # 如果有前一天数据，先用前一天数据更新模型
            if prev_day_df is not None:
                up, down = train_on_data(engine, model, prev_day_df)
            
            # 测试当天（不学习）
            signals, skipped = test_no_learn(engine, model, day_df, symbol)
            all_signals.extend(signals)
            total_skipped += skipped
            
            if signals:
                wins = sum(1 for s in signals if s['is_win'])
                pnl = sum(s['pnl'] for s in signals)
                wr = wins / len(signals) * 100
                print(f"    {test_month:02d}-{day:02d}: {len(signals):>3}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+7.0f}U")
            else:
                print(f"    {test_month:02d}-{day:02d}: 无信号")
            
            # 保存当天数据供明天训练
            prev_day_df = day_df
    
    # 汇总结果
    df = pd.DataFrame(all_signals).sort_values('timestamp').reset_index(drop=True)
    
    if len(df) == 0:
        print("\n⚠️ 没有产生任何信号")
        return
    
    days = (pd.to_datetime(df['timestamp']).max() - pd.to_datetime(df['timestamp']).min()).days + 1
    
    print(f"\n{'='*60}")
    print("测试结果统计 (每日预训练)")
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
    
    print(f"\n  按日期:")
    df['date'] = pd.to_datetime(df['timestamp']).dt.date
    for date in sorted(df['date'].unique()):
        ddf = df[df['date'] == date]
        wr = ddf['is_win'].mean() * 100
        pnl = ddf['pnl'].sum()
        print(f"    {date}: {len(ddf):>4}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+7.0f}U")
    
    output_file = project_root / "signals_daily_pretrain.csv"
    df['cumulative_pnl'] = df['pnl'].cumsum()
    df.to_csv(output_file, index=False)
    print(f"\n✅ 已保存到: {output_file}")


if __name__ == '__main__':
    main()
