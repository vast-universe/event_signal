#!/usr/bin/env python3
"""
预训练脚本 - 使用历史数据训练模型
"""
import os
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

import pandas as pd
import numpy as np
from datetime import datetime

from event_signal.config import SYMBOLS, HORIZON, OVERBOUGHT, OVERSOLD, SIGNAL_THRESHOLDS
from event_signal.core import FeatureEngine, Kline, RiverModel


def load_data(symbol: str, data_dir: str = "../data") -> pd.DataFrame:
    """加载历史数据"""
    files = sorted(Path(data_dir).glob(f"{symbol}-1m-*.csv"))
    if not files:
        print(f"⚠️ 未找到 {symbol} 数据文件")
        return pd.DataFrame()

    dfs = []
    for f in files:
        df = pd.read_csv(f, header=None,
                         names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                'taker_buy_quote', 'ignore'])
        dfs.append(df)

    data = pd.concat(dfs, ignore_index=True)
    data = data.sort_values('timestamp').drop_duplicates('timestamp')
    print(f"✅ 加载 {symbol}: {len(data)} 条K线")
    return data


def pretrain_symbol(symbol: str, data: pd.DataFrame) -> tuple:
    """预训练单个交易对，返回模型和分级统计"""
    model = RiverModel(horizon_minutes=HORIZON)
    features = FeatureEngine()

    horizon_bars = HORIZON

    stats = {
        'down': {'total': 0, 'correct': 0},
        'up': {'total': 0, 'correct': 0}
    }
    
    # 按置信度分级统计
    level_stats = {
        'down': {'S': [], 'A': [], 'B': [], 'C': []},
        'up': {'S': [], 'A': [], 'B': [], 'C': []}
    }

    for i in range(len(data) - horizon_bars):
        row = data.iloc[i]
        kline = Kline(
            timestamp=int(row['timestamp']),
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=float(row['volume'])
        )
        features.add_kline(kline)

        if not features.ready():
            continue

        feat = features.compute()
        if not feat:
            continue

        rsi6 = feat['rsi6']
        bb_pct = feat['bb_pct']
        entry_price = kline.close
        future_price = data.iloc[i + horizon_bars]['close']

        # 超买区域 → 训练做空模型
        if rsi6 >= OVERBOUGHT['rsi6_min'] and bb_pct >= OVERBOUGHT['bb_pct_min']:
            actual_down = future_price <= entry_price
            
            # 先预测，再学习
            prob = model.predict_down(feat)
            if prob >= 0.75:
                level_stats['down']['S'].append(1 if actual_down else 0)
            elif prob >= 0.70:
                level_stats['down']['A'].append(1 if actual_down else 0)
            elif prob >= 0.65:
                level_stats['down']['B'].append(1 if actual_down else 0)
            elif prob >= 0.60:
                level_stats['down']['C'].append(1 if actual_down else 0)
            
            model.model_down.learn_one(feat, actual_down)
            stats['down']['total'] += 1
            if actual_down:
                stats['down']['correct'] += 1

        # 超卖区域 → 训练做多模型
        elif rsi6 <= OVERSOLD['rsi6_max'] and bb_pct <= OVERSOLD['bb_pct_max']:
            actual_up = future_price >= entry_price
            
            # 先预测，再学习
            prob = model.predict_up(feat)
            if prob >= 0.75:
                level_stats['up']['S'].append(1 if actual_up else 0)
            elif prob >= 0.70:
                level_stats['up']['A'].append(1 if actual_up else 0)
            elif prob >= 0.65:
                level_stats['up']['B'].append(1 if actual_up else 0)
            elif prob >= 0.60:
                level_stats['up']['C'].append(1 if actual_up else 0)
            
            model.model_up.learn_one(feat, actual_up)
            stats['up']['total'] += 1
            if actual_up:
                stats['up']['correct'] += 1

    model.stats = stats
    return model, level_stats


def main():
    print("=" * 60)
    print("🚀 Event Signal 预训练")
    print("=" * 60)

    os.makedirs("models", exist_ok=True)

    for symbol in SYMBOLS:
        print(f"\n{'='*40}")
        print(f"训练 {symbol}")
        print('='*40)

        data = load_data(symbol)
        if data.empty:
            continue

        model, level_stats = pretrain_symbol(symbol, data)

        # 保存模型
        path = f"models/{symbol}.pkl"
        model.save(path)

        # 打印统计
        down = model.stats['down']
        up = model.stats['up']

        print(f"\n📊 训练结果:")
        if down['total'] > 0:
            acc = down['correct'] / down['total']
            print(f"  做空: {down['total']} 样本, 平均准确率 {acc:.1%}")
        if up['total'] > 0:
            acc = up['correct'] / up['total']
            print(f"  做多: {up['total']} 样本, 平均准确率 {acc:.1%}")
        
        # 打印分级统计
        print(f"\n📈 按置信度分级统计:")
        for direction in ['down', 'up']:
            d_name = "做空" if direction == 'down' else "做多"
            print(f"\n  {d_name}:")
            for level in ['S', 'A', 'B', 'C']:
                results = level_stats[direction][level]
                if results:
                    cnt = len(results)
                    wr = sum(results) / cnt
                    mark = "✅" if (level == 'S' and wr >= 0.75) or (level == 'A' and wr >= 0.70) else ""
                    print(f"    {level}级: {cnt:>5}笔, 胜率 {wr:.1%} {mark}")
                else:
                    print(f"    {level}级: 0笔")

        print(f"✅ 模型已保存: {path}")

    print("\n" + "=" * 60)
    print("✅ 预训练完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
