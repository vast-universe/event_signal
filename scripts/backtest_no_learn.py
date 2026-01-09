#!/usr/bin/env python3
"""
回测脚本 - 预训练后不学习测试
1. 用历史数据预训练模型
2. 在测试期间只预测，不学习
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
    BET_AMOUNTS
)

# 测试：C级阈值改成0.62
SIGNAL_THRESHOLDS = {"S": 0.75, "A": 0.70, "B": 0.65, "C": 0.62}
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


def load_klines_daily(symbol, year, month, days):
    """加载日度K线数据"""
    dfs = []
    for day in days:
        filepath = DATA_DIR / f'{symbol}-1m-{year}-{month:02d}-{day:02d}.csv'
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


def pretrain(symbol, df, warmup=1000):
    """预训练模型"""
    df = df.copy()
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
        price = row['close']
        future_price = row['future_price']
        
        if pd.isna(future_price):
            continue
        
        x = {col: float(feat[col]) for col in FEATURE_COLS}
        
        # 超买 → 做空
        if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
            y = 1 if future_price < price else 0
            model.model_down.learn_one(x, y)
            trained_down += 1
        
        # 超卖 → 做多
        elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
            y = 1 if future_price > price else 0
            model.model_up.learn_one(x, y)
            trained_up += 1
    
    return engine, model, trained_up, trained_down


def test_no_learn(symbol, engine, model, df):
    """测试阶段 - 只预测不学习"""
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
        
        # 超买 → 做空
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
            # 不学习！
        
        # 超卖 → 做多
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
            # 不学习！
    
    return signals, skipped_vol


def main():
    print("=" * 60)
    print("回测脚本 - 预训练后不学习测试")
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
        
        # 1. 加载训练数据
        print(f"\n[1] 加载训练数据 ({train_years[0]}-{train_years[-1]})...")
        train_df = load_klines_monthly(symbol, train_years)
        print(f"    训练数据: {len(train_df):,} 条K线")
        
        # 2. 预训练
        print(f"\n[2] 预训练模型...")
        engine, model, trained_up, trained_down = pretrain(symbol, train_df)
        print(f"    做多训练: {trained_up:,} 样本")
        print(f"    做空训练: {trained_down:,} 样本")
        
        # 3. 加载测试数据
        print(f"\n[3] 加载测试数据 ({test_year}-{test_month:02d}-01 ~ {test_year}-{test_month:02d}-{test_days[-1]:02d})...")
        test_df = load_klines_daily(symbol, test_year, test_month, test_days)
        print(f"    测试数据: {len(test_df):,} 条K线")
        
        # 4. 测试（不学习）
        print(f"\n[4] 测试（不学习）...")
        signals, skipped = test_no_learn(symbol, engine, model, test_df)
        all_signals.extend(signals)
        total_skipped += skipped
        print(f"    信号数: {len(signals)}")
        print(f"    vol_spike跳过: {skipped}")
    
    # 汇总结果
    df = pd.DataFrame(all_signals).sort_values('timestamp').reset_index(drop=True)
    
    if len(df) == 0:
        print("\n⚠️ 没有产生任何信号")
        return
    
    days = (pd.to_datetime(df['timestamp']).max() - pd.to_datetime(df['timestamp']).min()).days + 1
    
    print(f"\n{'='*60}")
    print("测试结果统计 (2026-01-01 ~ 2026-01-08)")
    print(f"{'='*60}")
    print(f"  总信号: {len(df)} 笔 ({len(df)/days:.1f}/天)")
    print(f"  总胜率: {df['is_win'].mean()*100:.1f}%")
    print(f"  总盈亏: {df['pnl'].sum():+.0f}U ({df['pnl'].sum()/days:+.1f}/天)")
    print(f"  vol_spike跳过: {total_skipped} 笔")
    
    print(f"\n  按等级:")
    for level in ['S', 'A', 'B', 'C']:
        ldf = df[df['level'] == level]
        if len(ldf) > 0:
            wr = ldf['is_win'].mean() * 100
            pnl = ldf['pnl'].sum()
            print(f"    {level}级: {len(ldf):>5}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+8.0f}U")
    
    print(f"\n  按方向:")
    for direction in ['LONG', 'SHORT']:
        ddf = df[df['direction'] == direction]
        if len(ddf) > 0:
            wr = ddf['is_win'].mean() * 100
            pnl = ddf['pnl'].sum()
            print(f"    {direction:>5}: {len(ddf):>5}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+8.0f}U")
    
    print(f"\n  按币种:")
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        sdf = df[df['symbol'] == symbol]
        if len(sdf) > 0:
            wr = sdf['is_win'].mean() * 100
            pnl = sdf['pnl'].sum()
            print(f"    {symbol}: {len(sdf):>5}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+8.0f}U")
    
    print(f"\n  按日期:")
    df['date'] = pd.to_datetime(df['timestamp']).dt.date
    for date in sorted(df['date'].unique()):
        ddf = df[df['date'] == date]
        wr = ddf['is_win'].mean() * 100
        pnl = ddf['pnl'].sum()
        print(f"    {date}: {len(ddf):>4}笔 | 胜率 {wr:.1f}% | 盈亏 {pnl:>+7.0f}U")
    
    output_file = project_root / "signals_no_learn_test.csv"
    df['cumulative_pnl'] = df['pnl'].cumsum()
    df.to_csv(output_file, index=False)
    print(f"\n✅ 已保存到: {output_file}")


if __name__ == '__main__':
    main()
