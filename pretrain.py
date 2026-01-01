#!/usr/bin/env python3
"""
预训练模型 - 用和test_tiered_betting.py一样的方式训练
1-9月训练，10-11月测试验证
"""
import os
import sys
import glob
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from river import linear_model, preprocessing, compose

from event_signal.model import RiverModel
from event_signal.config import HORIZON, OVERBOUGHT, OVERSOLD, SIGNAL_THRESHOLDS, BET_AMOUNTS


def load_data(symbol: str, months: list) -> pd.DataFrame:
    """加载历史数据"""
    dfs = []
    for m in months:
        path = f"data/{symbol}-1m-2025-{m:02d}.csv"
        if os.path.exists(path):
            df = pd.read_csv(path, header=None, names=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])
            dfs.append(df)
    
    if not dfs:
        raise FileNotFoundError(f"找不到数据: {symbol}")
    
    df = pd.concat(dfs, ignore_index=True)
    df['open_time'] = pd.to_datetime(df['open_time'] // 1000, unit='ms')
    return df


def calc_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算特征"""
    df = df.copy()
    
    # RSI
    delta = df['close'].diff()
    for p in [6, 14]:
        gain = delta.where(delta > 0, 0).rolling(window=p).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=p).mean()
        rs = gain / (loss + 1e-10)
        df[f'rsi{p}'] = 100 - (100 / (1 + rs))
    
    # 布林带位置
    bb_mid = df['close'].rolling(window=20).mean()
    bb_std = df['close'].rolling(window=20).std()
    df['bb_pct'] = (df['close'] - (bb_mid - 2*bb_std)) / (4*bb_std + 1e-10)
    
    # 成交量比率
    df['vol_ratio'] = df['volume'] / (df['volume'].rolling(window=20).mean() + 1e-10)
    
    # 收益率
    for p in [5, 10, 20]:
        df[f'ret{p}'] = df['close'].pct_change(p) * 100
    
    # K线形态
    df['body_pct'] = (df['close'] - df['open']) / df['open'] * 100
    df['upper_shadow'] = (df['high'] - df[['open', 'close']].max(axis=1)) / df['open'] * 100
    df['lower_shadow'] = (df[['open', 'close']].min(axis=1) - df['low']) / df['open'] * 100
    
    # 连续涨跌
    df['up_count'] = (df['close'] > df['close'].shift(1)).rolling(5).sum()
    
    # 波动率
    df['volatility'] = df['ret5'].rolling(window=20).std()
    
    # 目标：10分钟后涨跌
    df['target_down'] = (df['close'].shift(-HORIZON) < df['close']).astype(int)
    df['target_up'] = (df['close'].shift(-HORIZON) > df['close']).astype(int)
    
    return df


FEATURE_COLS = [
    'rsi6', 'rsi14', 'bb_pct', 'vol_ratio',
    'ret5', 'ret10', 'ret20',
    'body_pct', 'upper_shadow', 'lower_shadow',
    'up_count', 'volatility'
]


def get_features(row) -> dict:
    """提取特征字典"""
    return {col: float(row[col]) for col in FEATURE_COLS}


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


def pretrain_and_test():
    """预训练并测试"""
    train_months = list(range(1, 10))  # 1-9月
    test_months = [10, 11]  # 10-11月
    
    all_trades = []
    models = {}
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        print(f"\n{'='*60}")
        print(f"处理 {symbol}")
        print(f"{'='*60}")
        
        # 加载数据
        print("加载训练数据...")
        train_df = load_data(symbol, train_months)
        train_df = calc_features(train_df).dropna()
        print(f"  训练数据: {len(train_df)} 根K线")
        
        print("加载测试数据...")
        test_df = load_data(symbol, test_months)
        test_df = calc_features(test_df).dropna()
        print(f"  测试数据: {len(test_df)} 根K线")
        
        # 创建模型 - 使用 L2=0.5 正则化提高各级别胜率
        model_down = compose.Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(l2=0.5)
        )
        model_up = compose.Pipeline(
            preprocessing.StandardScaler(),
            linear_model.LogisticRegression(l2=0.5)
        )
        
        # 训练做空模型（超买区域）
        print("\n训练做空模型...")
        train_overbought = train_df[
            (train_df['rsi6'] > OVERBOUGHT['rsi6_min']) & 
            (train_df['bb_pct'] > OVERBOUGHT['bb_pct_min'])
        ]
        print(f"  超买样本: {len(train_overbought)}")
        
        for _, row in train_overbought.iterrows():
            x = get_features(row)
            y = int(row['target_down'])
            if not any(pd.isna(v) for v in x.values()):
                model_down.learn_one(x, y)
        
        # 训练做多模型（超卖区域）
        print("训练做多模型...")
        train_oversold = train_df[
            (train_df['rsi6'] < OVERSOLD['rsi6_max']) & 
            (train_df['bb_pct'] < OVERSOLD['bb_pct_max'])
        ]
        print(f"  超卖样本: {len(train_oversold)}")
        
        for _, row in train_oversold.iterrows():
            x = get_features(row)
            y = int(row['target_up'])
            if not any(pd.isna(v) for v in x.values()):
                model_up.learn_one(x, y)
        
        # 测试做空
        print("\n测试做空信号...")
        test_overbought = test_df[
            (test_df['rsi6'] > OVERBOUGHT['rsi6_min']) & 
            (test_df['bb_pct'] > OVERBOUGHT['bb_pct_min'])
        ]
        
        for _, row in test_overbought.iterrows():
            x = get_features(row)
            y = int(row['target_down'])
            if any(pd.isna(v) for v in x.values()):
                continue
            
            try:
                pred_proba = model_down.predict_proba_one(x)
                if pred_proba and 1 in pred_proba:
                    proba = pred_proba[1]
                    level = get_signal_level(proba)
                    
                    if level:
                        bet = BET_AMOUNTS[level]
                        is_win = y == 1
                        pnl = bet * 0.8 if is_win else -bet
                        
                        all_trades.append({
                            'symbol': symbol,
                            'direction': 'DOWN',
                            'proba': proba,
                            'level': level,
                            'bet': bet,
                            'win': is_win,
                            'pnl': pnl,
                        })
            except:
                pass
            
            model_down.learn_one(x, y)
        
        # 测试做多
        print("测试做多信号...")
        test_oversold = test_df[
            (test_df['rsi6'] < OVERSOLD['rsi6_max']) & 
            (test_df['bb_pct'] < OVERSOLD['bb_pct_max'])
        ]
        
        for _, row in test_oversold.iterrows():
            x = get_features(row)
            y = int(row['target_up'])
            if any(pd.isna(v) for v in x.values()):
                continue
            
            try:
                pred_proba = model_up.predict_proba_one(x)
                if pred_proba and 1 in pred_proba:
                    proba = pred_proba[1]
                    level = get_signal_level(proba)
                    
                    if level:
                        bet = BET_AMOUNTS[level]
                        is_win = y == 1
                        pnl = bet * 0.8 if is_win else -bet
                        
                        all_trades.append({
                            'symbol': symbol,
                            'direction': 'UP',
                            'proba': proba,
                            'level': level,
                            'bet': bet,
                            'win': is_win,
                            'pnl': pnl,
                        })
            except:
                pass
            
            model_up.learn_one(x, y)
        
        # 保存模型
        models[symbol] = {'down': model_down, 'up': model_up}
    
    # 打印结果
    print_results(all_trades)
    
    # 保存模型到RiverModel格式
    save_models(models)
    
    return all_trades


def print_results(all_trades):
    """打印测试结果"""
    if not all_trades:
        print("没有交易记录")
        return
    
    trades_df = pd.DataFrame(all_trades)
    
    print("\n" + "="*70)
    print("分级下单测试结果（10-11月样本外测试）")
    print("="*70)
    
    # 按等级统计
    print("\n按信号等级统计:")
    print("-"*70)
    print(f"{'等级':<8} {'交易数':<10} {'胜率':<10} {'下注':<10} {'总盈亏':<12} {'平均盈亏':<10}")
    print("-"*70)
    
    for level in ['S', 'A', 'B', 'C']:
        sub = trades_df[trades_df['level'] == level]
        if len(sub) > 0:
            total = len(sub)
            wins = sub['win'].sum()
            win_rate = wins / total
            total_pnl = sub['pnl'].sum()
            avg_pnl = total_pnl / total
            bet = sub['bet'].iloc[0]
            
            status = "✅" if win_rate > 0.556 else "❌"
            print(f"{level}级      {total:<10} {win_rate:.1%}      {bet}U        {total_pnl:.0f}        {avg_pnl:.2f}      {status}")
    
    # 总计
    print("-"*70)
    total = len(trades_df)
    wins = trades_df['win'].sum()
    total_pnl = trades_df['pnl'].sum()
    daily_pnl = total_pnl / 61  # 61天
    
    print(f"{'总计':<8} {total:<10} {wins/total:.1%}      -         {total_pnl:.0f}        {total_pnl/total:.2f}")
    print(f"\n每天交易: {total/61:.1f}笔")
    print(f"日均盈亏: {daily_pnl:.1f} USDT")
    
    # 按币种和方向
    print("\n" + "="*70)
    print("按币种和方向统计")
    print("="*70)
    
    for symbol in ['BTCUSDT', 'ETHUSDT']:
        for direction in ['DOWN', 'UP']:
            sub = trades_df[(trades_df['symbol'] == symbol) & (trades_df['direction'] == direction)]
            if len(sub) > 0:
                total = len(sub)
                wins = sub['win'].sum()
                pnl = sub['pnl'].sum()
                print(f"{symbol} {direction}: {total}笔, 胜率{wins/total:.1%}, 盈亏{pnl:.0f}")


def save_models(models):
    """保存模型"""
    os.makedirs("event_signal/models", exist_ok=True)
    
    for symbol, m in models.items():
        # 创建RiverModel并替换内部模型
        river_model = RiverModel(horizon_minutes=HORIZON)
        river_model.model_down = m['down']
        river_model.model_up = m['up']
        
        path = f"event_signal/models/{symbol}.pkl"
        river_model.save(path)
        print(f"模型已保存: {path}")


def main():
    pretrain_and_test()
    print("\n" + "="*60)
    print("预训练完成！现在可以运行: python event_signal/run.py")
    print("="*60)


if __name__ == "__main__":
    main()
