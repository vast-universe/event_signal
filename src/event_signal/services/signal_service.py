"""
信号服务 - 核心业务逻辑
"""
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Optional

import aiohttp
import websockets

from ..config import SYMBOLS, HORIZON, PAYOUT_RATE, BREAKEVEN_WINRATE
from ..core import FeatureEngine, Kline, RiverModel, Strategy, Signal
from ..db import get_db
from ..db.models import SignalRepository
from ..api.websocket import ws_manager


class SymbolHandler:
    """单个交易对处理器"""

    def __init__(self, symbol: str, model: Optional[RiverModel] = None):
        self.symbol = symbol
        self.features = FeatureEngine()
        self.model = model or RiverModel(horizon_minutes=HORIZON)
        self.strategy = Strategy(symbol)
        self.pending_signals: list = []
        self.last_features: dict = {}
        self.daily_pnl = 0.0
        self.daily_signals = 0
        # 延迟学习队列：存储待学习的样本
        self._pending_learn: list = []

    async def on_kline_close(self, kline: Kline, price: float, db) -> Optional[Signal]:
        """处理K线收盘"""
        current_ts = int(datetime.utcnow().timestamp() * 1000)

        self.features.add_kline(kline)

        # 先处理延迟学习（用当前价格作为 future_price）
        self._learn_pending_samples(price, current_ts)

        await self._settle_signals(current_ts, price, db)

        if not self.features.ready():
            return None

        feat = self.features.compute()
        if not feat:
            return None

        self.last_features = feat

        rsi6 = feat.get('rsi6', 50)
        bb_pct = feat.get('bb_pct', 0.5)

        prob_down = self.model.predict_down(feat)
        prob_up = self.model.predict_up(feat)

        # 推送 ticker 数据（每分钟K线收盘时）
        await ws_manager.send_ticker({
            'symbol': self.symbol,
            'price': price,
            'rsi6': rsi6,
            'rsi14': feat.get('rsi14', 50),
            'bb_pct': bb_pct,
            'prob_down': prob_down,
            'prob_up': prob_up,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
        })

        # 推送 K 线数据（实时更新前端 K 线列表）
        from datetime import timedelta
        kline_utc = datetime.utcfromtimestamp(kline.timestamp / 1000)
        kline_bj = kline_utc + timedelta(hours=8)
        await ws_manager.send_kline({
            'symbol': self.symbol,
            'timestamp': kline.timestamp,
            'time_utc': kline_utc.isoformat() + 'Z',
            'time_beijing': kline_bj.strftime('%Y-%m-%d %H:%M'),
            'open': kline.open,
            'high': kline.high,
            'low': kline.low,
            'close': kline.close,
            'volume': kline.volume,
            'total_klines': len(self.features.klines),
        })

        signal = self.strategy.check(price, feat, prob_down, prob_up)

        # 延迟学习：添加样本到队列（无论是否产生信号）
        if feat:
            rsi6 = feat.get('rsi6', 50)
            bb_pct = feat.get('bb_pct', 0.5)
            from ..config import OVERBOUGHT, OVERSOLD
            
            if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
                # 超买做空
                self.model.add_pending(current_ts, feat, price, "DOWN")
            elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
                # 超卖做多
                self.model.add_pending(current_ts, feat, price, "UP")

        if signal:
            settle_ts = current_ts + HORIZON * 60 * 1000

            async with db.session() as session:
                repo = SignalRepository(session)
                db_signal = await repo.create({
                    'symbol': self.symbol,
                    'direction': signal.direction,
                    'level': signal.level,
                    'confidence': signal.confidence,
                    'entry_price': signal.price,
                    'bet_amount': signal.bet_amount,
                })
                signal_id = db_signal.id

            self.pending_signals.append({
                'id': signal_id,
                'settle_ts': settle_ts,
                'direction': signal.direction,
                'level': signal.level,
                'entry_price': signal.price,
                'confidence': signal.confidence,
                'bet_amount': signal.bet_amount,
            })

            self.daily_signals += 1

            await ws_manager.send_signal({
                'id': signal_id,
                'symbol': self.symbol,
                'direction': signal.direction,
                'level': signal.level,
                'confidence': signal.confidence,
                'entry_price': signal.price,
                'bet_amount': signal.bet_amount,
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'status': 'pending',
            })

        return signal

    async def _settle_signals(self, current_ts: int, current_price: float, db):
        """结算到期信号"""
        remaining = []

        for sig in self.pending_signals:
            if current_ts >= sig['settle_ts']:
                entry = sig['entry_price']
                bet = sig['bet_amount']

                if sig['direction'] == "DOWN":
                    is_win = current_price <= entry
                else:
                    is_win = current_price >= entry

                pnl = bet * PAYOUT_RATE if is_win else -bet
                self.daily_pnl += pnl
                self.strategy.record_result(is_win, pnl)

                async with db.session() as session:
                    repo = SignalRepository(session)
                    await repo.update_settled(sig['id'], current_price, is_win, pnl)

                await ws_manager.send_settlement({
                    'id': sig['id'],
                    'symbol': self.symbol,
                    'direction': sig['direction'],
                    'level': sig['level'],
                    'entry_price': entry,
                    'settle_price': current_price,
                    'settle_at': datetime.utcnow().isoformat() + 'Z',
                    'is_win': is_win,
                    'pnl': pnl,
                })

                result = "✅赢" if is_win else "❌输"
                d = "做空" if sig['direction'] == "DOWN" else "做多"
                ret = (current_price / entry - 1) * 100
                print(f"\n📊 结算 {self.symbol} {d}({sig['level']}级) | "
                      f"${entry:,.2f}→${current_price:,.2f} ({ret:+.2f}%) | "
                      f"{result} {pnl:+.1f}U | 日盈亏:{self.daily_pnl:+.1f}U")
            else:
                remaining.append(sig)

        self.pending_signals = remaining

    def _learn_pending_samples(self, current_price: float, current_ts: int):
        """
        延迟学习：已启用
        
        研究结果显示 RMSProp + 延迟学习效果最好：
        - RMSProp延迟学习：914信号, 60.3%胜率, +1352U
        - RMSProp不学习：1753信号, 51.7%胜率, -2714U
        """
        # 调用模型的延迟学习
        results = self.model.update_with_price(current_ts, current_price)
        return results


class SignalService:
    """信号服务"""

    def __init__(self, symbols: list = None, data_dir: str = None):
        """
        初始化信号服务
        
        Args:
            symbols: 交易对列表
            data_dir: 数据目录路径
        """
        self.symbols = symbols or SYMBOLS
        self.handlers: Dict[str, SymbolHandler] = {}
        self.ws = None
        self.db = None
        self._lines = {}
        self._current_prices: Dict[str, float] = {}
        self._settlement_task = None
        self.data_dir = data_dir

        # 初始化 handlers（模型稍后在 start() 中加载）
        for symbol in self.symbols:
            self.handlers[symbol] = SymbolHandler(symbol, None)

    def _train_from_local_data(self, symbol: str, data_dir: str, 
                                train_years: list = None) -> Optional[tuple]:
        """
        从本地data目录训练模型（与回测脚本完全一致的逻辑）
        只训练历史数据（到昨天为止），不包含当天数据
        """
        import pandas as pd
        from pathlib import Path
        from ..config import OVERBOUGHT, OVERSOLD
        
        if train_years is None:
            train_years = [2024, 2025]
        
        data_path = Path(data_dir)
        
        # 计算今天0点的时间戳（UTC），只训练到昨天
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_ts = int(today_start.timestamp() * 1000)
        
        # 加载本地数据
        dfs = []
        for year in train_years:
            for month in range(1, 13):
                filepath = data_path / f"{symbol}-1m-{year}-{month:02d}.csv"
                if filepath.exists():
                    df = pd.read_csv(filepath, header=None,
                                   names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                          'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                          'taker_buy_quote', 'ignore'])
                    if df['timestamp'].iloc[0] > 1e15:
                        df['timestamp'] = df['timestamp'] // 1000
                    dfs.append(df)
        
        # 也加载当年的本地数据（如果有）
        current_year = datetime.utcnow().year
        if current_year not in train_years:
            for month in range(1, 13):
                filepath = data_path / f"{symbol}-1m-{current_year}-{month:02d}.csv"
                if filepath.exists():
                    df = pd.read_csv(filepath, header=None,
                                   names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                          'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                          'taker_buy_quote', 'ignore'])
                    if df['timestamp'].iloc[0] > 1e15:
                        df['timestamp'] = df['timestamp'] // 1000
                    dfs.append(df)
            # 也检查日级文件（格式：{symbol}-1m-2026-01-05.csv）
            for month in range(1, 13):
                for day in range(1, 32):
                    filepath = data_path / f"{symbol}-1m-{current_year}-{month:02d}-{day:02d}.csv"
                    if filepath.exists():
                        df = pd.read_csv(filepath, header=None,
                                       names=['timestamp', 'open', 'high', 'low', 'close', 'volume',
                                              'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                                              'taker_buy_quote', 'ignore'])
                        if df['timestamp'].iloc[0] > 1e15:
                            df['timestamp'] = df['timestamp'] // 1000
                        dfs.append(df)
        
        if not dfs:
            print(f"  ⚠️ {symbol}: 未找到训练数据")
            return None
        
        data = pd.concat(dfs, ignore_index=True).sort_values('timestamp').drop_duplicates('timestamp')
        
        # 只保留今天之前的数据（不包含当天）
        data = data[data['timestamp'] < today_start_ts]
        
        if len(data) == 0:
            print(f"  ⚠️ {symbol}: 过滤后无训练数据")
            return None
        
        data['future_price'] = data['close'].shift(-HORIZON)
        
        last_ts = int(data['timestamp'].max())
        print(f"  {symbol}: 加载 {len(data):,} 条本地K线 (截止 {datetime.utcfromtimestamp(last_ts/1000).strftime('%Y-%m-%d %H:%M')} UTC)")
        print(f"    训练数据截止到昨天，不包含今天 ({today_start.strftime('%Y-%m-%d')} UTC)")
        
        # 初始化模型和特征引擎
        model = RiverModel(horizon_minutes=HORIZON)
        engine = FeatureEngine(window_size=100)
        
        trained_up, trained_down, level_stats = self._train_on_dataframe(model, engine, data, symbol)
        
        print(f"  {symbol}: 本地数据训练完成 - 做空 {trained_down:,} 样本, 做多 {trained_up:,} 样本")
        print(f"    特征引擎 K 线数: {len(engine.klines)}")
        
        # 显示模型训练统计
        down_stats = model.stats['down']
        up_stats = model.stats['up']
        down_acc = down_stats['correct'] / down_stats['total'] * 100 if down_stats['total'] > 0 else 0
        up_acc = up_stats['correct'] / up_stats['total'] * 100 if up_stats['total'] > 0 else 0
        print(f"    模型统计:")
        print(f"      做空: {down_stats['total']:,} 样本, 正确 {down_stats['correct']:,}, 准确率 {down_acc:.1f}%")
        print(f"      做多: {up_stats['total']:,} 样本, 正确 {up_stats['correct']:,}, 准确率 {up_acc:.1f}%")
        
        # 显示按等级的信号胜率
        total_signals = sum(s['total'] for s in level_stats.values())
        total_wins = sum(s['wins'] for s in level_stats.values())
        overall_wr = total_wins / total_signals * 100 if total_signals > 0 else 0
        print(f"    信号统计 (warmup后): {total_signals:,} 笔, 总胜率 {overall_wr:.1f}%")
        for level in ['S', 'A', 'B', 'C']:
            s = level_stats[level]
            if s['total'] > 0:
                wr = s['wins'] / s['total'] * 100
                status = "✅" if wr >= 55.6 else "⚠️"
                print(f"      {level}级: {s['total']:,} 笔, 胜率 {wr:.1f}% {status}")
        
        # 显示训练后特征引擎最后 5 根 K 线
        from datetime import timedelta
        print(f"    训练后最后 5 根 K 线:")
        for k in list(engine.klines)[-5:]:
            dt_utc = datetime.utcfromtimestamp(k.timestamp / 1000)
            dt_bj = dt_utc + timedelta(hours=8)
            print(f"      {dt_utc.strftime('%Y-%m-%d %H:%M')} UTC = {dt_bj.strftime('%H:%M')} 北京 | close={k.close:.2f}")
        
        # 把训练好的特征引擎也传给 handler
        self.handlers[symbol].features = engine
        
        return model, last_ts
    
    def _train_on_dataframe(self, model: RiverModel, engine: FeatureEngine, 
                            data, symbol: str, warmup: int = 1000) -> tuple:
        """
        在 DataFrame 上训练模型 - 与回测完全一致的 predict-then-learn 模式
        
        关键：先预测，再学习，这样模型状态和回测一致
        返回: (trained_up, trained_down, level_stats)
        """
        import pandas as pd
        from ..config import OVERBOUGHT, OVERSOLD, SIGNAL_THRESHOLDS, VOL_SPIKE_MAX
        
        FEATURE_COLS = [
            'rsi6', 'rsi14', 'bb_pct', 'vol_ratio',
            'ret5', 'ret10', 'ret20',
            'body_pct', 'upper_shadow', 'lower_shadow',
            'up_count', 'volatility'
        ]
        
        trained_up = 0
        trained_down = 0
        
        # 按等级统计信号胜率
        level_stats = {
            'S': {'total': 0, 'wins': 0},
            'A': {'total': 0, 'wins': 0},
            'B': {'total': 0, 'wins': 0},
            'C': {'total': 0, 'wins': 0},
        }
        
        def get_level(prob):
            for level, threshold in SIGNAL_THRESHOLDS.items():
                if prob >= threshold:
                    return level
            return None
        
        for _, row in data.iterrows():
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
            
            if future_price is None or pd.isna(future_price):
                continue
            
            x = {col: float(feat[col]) for col in FEATURE_COLS}
            
            # 超买 → 做空训练 (predict-then-learn)
            if rsi6 >= OVERBOUGHT["rsi6_min"] and bb_pct >= OVERBOUGHT["bb_pct_min"]:
                y = 1 if future_price <= price else 0  # 结算价 <= 下单价 为赢
                
                # warmup 后先预测，统计信号胜率
                if trained_down >= warmup and vol_spike <= VOL_SPIKE_MAX:
                    proba = model.model_down.predict_proba_one(x)
                    if proba:
                        p = proba.get(True, proba.get(1, 0.5))
                        level = get_level(p)
                        if level:
                            level_stats[level]['total'] += 1
                            if y == 1:
                                level_stats[level]['wins'] += 1
                
                model.model_down.learn_one(x, y)
                model.stats['down']['total'] += 1
                if y == 1:
                    model.stats['down']['correct'] += 1
                trained_down += 1
            
            # 超卖 → 做多训练 (predict-then-learn)
            elif rsi6 <= OVERSOLD["rsi6_max"] and bb_pct <= OVERSOLD["bb_pct_max"]:
                y = 1 if future_price >= price else 0  # 结算价 >= 下单价 为赢
                
                # warmup 后先预测，统计信号胜率
                if trained_up >= warmup and vol_spike <= VOL_SPIKE_MAX:
                    proba = model.model_up.predict_proba_one(x)
                    if proba:
                        p = proba.get(True, proba.get(1, 0.5))
                        level = get_level(p)
                        if level:
                            level_stats[level]['total'] += 1
                            if y == 1:
                                level_stats[level]['wins'] += 1
                
                model.model_up.learn_one(x, y)
                model.stats['up']['total'] += 1
                if y == 1:
                    model.stats['up']['correct'] += 1
                trained_up += 1
        
        return trained_up, trained_down, level_stats
    
    async def _fetch_and_fill_features(self, symbol: str, engine: FeatureEngine, start_ts: int):
        """
        从 API 获取历史 K 线，只填充特征引擎，不训练模型
        
        这样可以保证模型状态和回测一致（只用本地数据训练）
        API 数据只用于让特征引擎有足够的历史数据来计算指标
        
        注意：
        - current_ts 在调用时获取（训练完成后），而不是服务启动时
        - API 只获取到上一分钟的完整 K 线，当前分钟由 WS 补充
        """
        from datetime import timedelta
        
        url = "https://api.binance.com/api/v3/klines"
        # 训练完成后获取当前时间（不是服务启动时间）
        now = datetime.utcnow()
        # 只获取到上一分钟（当前分钟的 K 线还没结束，由 WS 补充）
        # 例如：05:25:30 → 只获取到 05:24:00 的 K 线
        end_ts = int(now.replace(second=0, microsecond=0).timestamp() * 1000)
        
        # 计算需要获取多少条K线
        api_start_ts = start_ts + 60000  # 从本地数据最后一条的下一分钟开始
        minutes_gap = (end_ts - start_ts) // 60000
        
        if minutes_gap <= 0:
            print(f"  {symbol}: 本地数据已是最新，无需API补齐")
            return 0
        
        start_utc = datetime.utcfromtimestamp(api_start_ts / 1000)
        end_utc = datetime.utcfromtimestamp(end_ts / 1000)
        print(f"  {symbol}: 需要补齐 {minutes_gap:,} 分钟")
        print(f"    从 {start_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"    到 {end_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC (上一分钟)")
        
        total_klines = 0
        first_ts = None
        last_ts = None
        
        async with aiohttp.ClientSession() as session:
            fetch_start = api_start_ts
            
            while fetch_start < end_ts:
                params = {
                    "symbol": symbol,
                    "interval": "1m",
                    "startTime": fetch_start,
                    "endTime": end_ts - 1,  # 不包含当前分钟
                    "limit": 1000
                }
                
                try:
                    async with session.get(url, params=params) as resp:
                        if resp.status != 200:
                            print(f"  {symbol}: API请求失败 {resp.status}")
                            break
                        
                        klines = await resp.json()
                        if not klines:
                            break
                        
                        # 只添加到特征引擎，不训练
                        for k in klines:
                            kline = Kline(
                                timestamp=int(k[0]),
                                open=float(k[1]),
                                high=float(k[2]),
                                low=float(k[3]),
                                close=float(k[4]),
                                volume=float(k[5])
                            )
                            engine.add_kline(kline)
                            
                            if first_ts is None:
                                first_ts = int(k[0])
                            last_ts = int(k[0])
                            total_klines += 1
                        
                        # 更新起始时间
                        fetch_start = int(klines[-1][0]) + 60000
                        
                except Exception as e:
                    print(f"  {symbol}: API请求异常 - {e}")
                    break
        
        if total_klines > 0 and first_ts and last_ts:
            first_utc = datetime.utcfromtimestamp(first_ts / 1000)
            last_utc = datetime.utcfromtimestamp(last_ts / 1000)
            print(f"  {symbol}: API补齐完成 - {total_klines:,} 条K线")
            print(f"    时间范围: {first_utc.strftime('%Y-%m-%d %H:%M')} - {last_utc.strftime('%Y-%m-%d %H:%M')} UTC")
            
            # 显示补齐后特征引擎最后 5 根 K 线
            print(f"    补齐后最后 5 根 K 线:")
            for k in list(engine.klines)[-5:]:
                dt_utc = datetime.utcfromtimestamp(k.timestamp / 1000)
                dt_bj = dt_utc + timedelta(hours=8)
                print(f"      {dt_utc.strftime('%Y-%m-%d %H:%M')} UTC = {dt_bj.strftime('%H:%M')} 北京 | close={k.close:.2f}")
        else:
            print(f"  {symbol}: API补齐完成 - {total_klines} 条K线")
        
        return total_klines

    async def _recover_pending_signals(self):
        """启动时恢复待结算信号"""
        print("恢复待结算信号...")
        async with self.db.session() as session:
            repo = SignalRepository(session)
            pending = await repo.get_pending()
            
            current_ts = int(datetime.utcnow().timestamp() * 1000)
            
            for sig in pending:
                # 计算结算时间戳 (创建时间 + 10分钟)
                # created_at 是 UTC naive datetime，需要手动转换
                from calendar import timegm
                created_utc_ts = timegm(sig.created_at.timetuple())
                settle_ts = int((created_utc_ts + HORIZON * 60) * 1000)
                
                # 如果已经过期，立即标记为需要结算
                if settle_ts < current_ts:
                    print(f"  ⚠️ 信号 {sig.id} 已过期，等待结算")
                
                handler = self.handlers.get(sig.symbol)
                if handler:
                    handler.pending_signals.append({
                        'id': sig.id,
                        'settle_ts': settle_ts,
                        'direction': sig.direction,
                        'level': sig.level,
                        'entry_price': sig.entry_price,
                        'confidence': sig.confidence,
                        'bet_amount': sig.bet_amount,
                    })
            
            if pending:
                print(f"  恢复 {len(pending)} 个待结算信号")
            else:
                print("  无待结算信号")

    async def _fetch_history(self, skip_if_trained: bool = False):
        """获取历史K线数据
        
        Args:
            skip_if_trained: 如果已从本地数据训练，跳过API获取（特征引擎已有数据）
        """
        url = "https://api.binance.com/api/v3/klines"

        async with aiohttp.ClientSession() as session:
            for symbol in self.symbols:
                handler = self.handlers[symbol]
                
                # 如果已从本地数据训练，特征引擎已有足够数据，跳过
                if skip_if_trained and handler.features.ready():
                    print(f"  {symbol}: 已从本地数据训练，跳过API获取 ({len(handler.features.klines)} 根K线)")
                    continue
                
                print(f"获取 {symbol} 历史K线...")
                params = {"symbol": symbol, "interval": "1m", "limit": 100}

                try:
                    async with session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()

                            for k in data[:-1]:
                                kline = Kline(
                                    timestamp=k[0],
                                    open=float(k[1]),
                                    high=float(k[2]),
                                    low=float(k[3]),
                                    close=float(k[4]),
                                    volume=float(k[5])
                                )
                                handler.features.add_kline(kline)

                            print(f"  {symbol}: 加载 {len(handler.features.klines)} 根K线")
                except Exception as e:
                    print(f"  {symbol}: 获取失败 - {e}")

    async def _on_kline_message(self, data: dict):
        k = data.get('k', {})
        symbol = k.get('s')
        is_closed = k.get('x', False)

        if symbol not in self.handlers:
            return

        handler = self.handlers[symbol]
        price = float(k.get('c', 0))
        self._current_prices[symbol] = price  # 更新实时价格缓存

        if is_closed:
            kline = Kline(
                timestamp=k.get('t'),
                open=float(k.get('o')),
                high=float(k.get('h')),
                low=float(k.get('l')),
                close=float(k.get('c')),
                volume=float(k.get('v'))
            )

            signal = await handler.on_kline_close(kline, price, self.db)

            if signal:
                self._print_signal(signal)

        # 实时日志已关闭
        # self._print_status(symbol, price, is_closed)

    def _print_status(self, symbol: str, price: float, is_closed: bool):
        handler = self.handlers[symbol]
        feat = handler.last_features

        rsi6 = feat.get('rsi6', 50)
        bb_pct = feat.get('bb_pct', 0.5)

        win_rate = handler.strategy.win_rate
        total = handler.strategy.total_signals

        status = "收盘" if is_closed else "实时"
        now = datetime.now().strftime("%H:%M:%S")

        if rsi6 >= 60 and bb_pct >= 0.7:
            indicator = f"🔴超买 RSI={rsi6:.0f}"
        elif rsi6 <= 40 and bb_pct <= 0.3:
            indicator = f"🟢超卖 RSI={rsi6:.0f}"
        else:
            indicator = f"RSI={rsi6:.0f} BB={bb_pct:.2f}"

        if total > 0:
            wr_str = f"胜率={win_rate:.0%}({total})"
            if win_rate >= BREAKEVEN_WINRATE:
                wr_str = f"✅{wr_str}"
            else:
                wr_str = f"⚠️{wr_str}"
        else:
            wr_str = "等待信号..."

        pending = len(handler.pending_signals)
        pnl = handler.daily_pnl

        line = (f"[{now}] [{status}] {symbol} ${price:,.2f} | "
                f"{indicator} | {wr_str} | "
                f"待:{pending} 盈亏:{pnl:+.1f}U")

        self._lines[symbol] = line

        btc = self._lines.get('BTCUSDT', 'BTCUSDT: 等待数据...')
        eth = self._lines.get('ETHUSDT', 'ETHUSDT: 等待数据...')

        # 清屏并显示两行
        print(f"\033[2K\r{btc}", end='')
        print(f"\n\033[2K\r{eth}", end='')
        print("\033[1A", end='', flush=True)  # 光标回到第一行

    def _print_signal(self, signal: Signal):
        print()
        print("=" * 70)
        print(signal)
        print(f"  时间: {signal.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  详情: {signal.details}")
        print(f"  结算: {HORIZON}分钟后")
        print("=" * 70)
        print()

    async def _settlement_loop(self):
        """独立的结算循环，每秒检查一次"""
        while True:
            try:
                # 使用 UTC 时间戳
                current_ts = int(datetime.utcnow().timestamp() * 1000)
                
                for symbol, handler in self.handlers.items():
                    price = self._current_prices.get(symbol)
                    if price and handler.pending_signals:
                        await handler._settle_signals(current_ts, price, self.db)
                
                await asyncio.sleep(1)
            except Exception as e:
                print(f"\n⚠️ 结算循环错误: {e}")
                await asyncio.sleep(1)

    async def connect_binance_ws(self):
        streams = "/".join([f"{s.lower()}@kline_1m" for s in self.symbols])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        print(f"连接 Binance WebSocket...")

        async for ws in websockets.connect(url, ping_interval=20):
            try:
                self.ws = ws
                print("✅ Binance WebSocket 已连接\n")

                async for message in ws:
                    data = json.loads(message)
                    if 'data' in data:
                        await self._on_kline_message(data['data'])
            except websockets.ConnectionClosed:
                print("\n⚠️ WebSocket 断开，重连中...")
                continue
            except Exception as e:
                print(f"\n❌ WebSocket 错误: {e}")
                await asyncio.sleep(5)
                continue

    async def start(self):
        """启动服务"""
        print("=" * 70)
        print("🚀 Event Signal 服务")
        print("=" * 70)
        print(f"  交易对: {', '.join(self.symbols)}")
        print(f"  预测周期: {HORIZON}分钟")
        print(f"  盈亏平衡胜率: {BREAKEVEN_WINRATE:.2%}")
        print(f"  训练模式: 本地数据训练 + 延迟学习（与回测一致的 predict-then-learn）")
        print("=" * 70)

        # 从本地数据训练模型
        last_timestamps = {}
        
        if self.data_dir:
            print(f"\n📚 从本地数据训练模型...")
            for symbol in self.symbols:
                result = self._train_from_local_data(symbol, self.data_dir)
                if result:
                    model, last_ts = result
                    self.handlers[symbol].model = model
                    last_timestamps[symbol] = last_ts
                else:
                    # 训练失败，使用新模型
                    self.handlers[symbol].model = RiverModel(horizon_minutes=HORIZON)
            
            # 从 API 补齐特征引擎（不训练模型，保持和回测一致）
            if last_timestamps:
                print(f"\n🌐 从API补齐特征引擎（不训练，保持模型和回测一致）...")
                for symbol in self.symbols:
                    if symbol in last_timestamps:
                        handler = self.handlers[symbol]
                        await self._fetch_and_fill_features(
                            symbol, 
                            handler.features,
                            last_timestamps[symbol]
                        )
        else:
            print(f"\n⚠️ 未指定数据目录，使用新模型...")
            for symbol in self.symbols:
                self.handlers[symbol].model = RiverModel(horizon_minutes=HORIZON)

        self.db = await get_db()
        await self._recover_pending_signals()
        
        # 如果从本地数据训练，特征引擎已有数据，可以跳过API获取
        await self._fetch_history(skip_if_trained=bool(last_timestamps))

        print("\n✅ 初始化完成，开始监听K线...")
        print()
        print()
        
        # 启动独立的结算任务
        self._settlement_task = asyncio.create_task(self._settlement_loop())

    async def stop(self):
        """停止服务"""
        if self._settlement_task:
            self._settlement_task.cancel()
        if self.ws:
            await self.ws.close()
        if self.db:
            await self.db.disconnect()
