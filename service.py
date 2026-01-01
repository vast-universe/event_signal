"""
Event Signal 主服务
实时接收K线数据，计算特征，生成交易信号
"""
import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Optional

import aiohttp
import websockets

from .config import (
    SYMBOLS, HORIZON, MODEL_SAVE_INTERVAL, BREAKEVEN_WINRATE,
    PAYOUT_RATE, get_signal_level, get_bet_amount
)
from .features import FeatureEngine, Kline
from .model import RiverModel
from .strategy import Strategy, Signal


class SymbolHandler:
    """单个交易对处理器"""
    
    def __init__(self, symbol: str, model: Optional[RiverModel] = None):
        self.symbol = symbol
        self.features = FeatureEngine()
        self.model = model or RiverModel(horizon_minutes=HORIZON)
        self.strategy = Strategy(symbol)
        
        # 待结算信号
        self.pending_signals: list = []
        self.last_features: dict = {}
        
        # 日统计
        self.daily_pnl = 0.0
        self.daily_signals = 0
    
    def on_kline_close(self, kline: Kline, price: float) -> Optional[Signal]:
        """处理K线收盘"""
        current_ts = int(datetime.now().timestamp() * 1000)
        
        # 添加K线
        self.features.add_kline(kline)
        
        # 更新模型（处理到期样本）
        self.model.update_with_price(current_ts, price)
        
        # 检查待结算信号
        self._settle_signals(current_ts, price)
        
        # 计算特征
        if not self.features.ready():
            return None
        
        feat = self.features.compute()
        if not feat:
            return None
        
        self.last_features = feat
        
        # 模型预测
        prob_down = self.model.predict_down(feat)
        prob_up = self.model.predict_up(feat)
        
        # 检查信号
        signal = self.strategy.check(price, feat, prob_down, prob_up)
        
        if signal:
            # 记录待结算信号
            settle_ts = current_ts + HORIZON * 60 * 1000
            self.pending_signals.append({
                'settle_ts': settle_ts,
                'direction': signal.direction,
                'level': signal.level,
                'entry_price': signal.price,
                'confidence': signal.confidence,
                'bet_amount': signal.bet_amount,
            })
            
            # 添加学习样本
            self.model.add_pending(kline.timestamp, feat, kline.close, signal.direction)
            self.daily_signals += 1
        
        return signal
    
    def _settle_signals(self, current_ts: int, current_price: float):
        """结算到期信号"""
        remaining = []
        
        for sig in self.pending_signals:
            if current_ts >= sig['settle_ts']:
                entry = sig['entry_price']
                bet = sig['bet_amount']
                
                # 判断胜负
                if sig['direction'] == "DOWN":
                    is_win = current_price <= entry
                else:
                    is_win = current_price >= entry
                
                # 计算盈亏
                pnl = bet * PAYOUT_RATE if is_win else -bet
                self.daily_pnl += pnl
                
                # 记录结果
                self.strategy.record_result(is_win, pnl)
                
                # 打印结算结果
                result = "✅赢" if is_win else "❌输"
                d = "做空" if sig['direction'] == "DOWN" else "做多"
                ret = (current_price / entry - 1) * 100
                print(f"\n📊 结算 {self.symbol} {d}({sig['level']}级) | "
                      f"${entry:,.2f}→${current_price:,.2f} ({ret:+.2f}%) | "
                      f"{result} {pnl:+.1f}U | 日盈亏:{self.daily_pnl:+.1f}U")
            else:
                remaining.append(sig)
        
        self.pending_signals = remaining


class EventSignalService:
    """Event Signal 服务"""
    
    def __init__(self, symbols: list = None, load_pretrained: bool = True):
        self.symbols = symbols or SYMBOLS
        self.handlers: Dict[str, SymbolHandler] = {}
        self.last_save_time = datetime.now()
        self.ws = None
        
        # 初始化处理器
        for symbol in self.symbols:
            model = None
            if load_pretrained:
                model = self._load_model(symbol)
            self.handlers[symbol] = SymbolHandler(symbol, model)
    
    def _load_model(self, symbol: str) -> Optional[RiverModel]:
        """加载预训练模型"""
        path = f"event_signal/models/{symbol}.pkl"
        if os.path.exists(path):
            try:
                model = RiverModel.load(path)
                print(f"✅ 加载模型: {symbol} (样本数: {model.total_samples})")
                return model
            except Exception as e:
                print(f"⚠️ 加载模型失败: {symbol} - {e}")
        return None
    
    def _save_models(self):
        """保存所有模型"""
        os.makedirs("event_signal/models", exist_ok=True)
        for symbol, handler in self.handlers.items():
            path = f"event_signal/models/{symbol}.pkl"
            handler.model.save(path)
        print(f"\n💾 模型已保存 ({datetime.now().strftime('%H:%M:%S')})")
    
    async def _fetch_history(self):
        """获取历史K线填充缓存"""
        url = "https://api.binance.com/api/v3/klines"
        
        async with aiohttp.ClientSession() as session:
            for symbol in self.symbols:
                print(f"获取 {symbol} 历史K线...")
                params = {"symbol": symbol, "interval": "1m", "limit": 100}
                
                try:
                    async with session.get(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            handler = self.handlers[symbol]
                            
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

    def _on_kline_message(self, data: dict):
        """处理K线消息"""
        k = data.get('k', {})
        symbol = k.get('s')
        is_closed = k.get('x', False)
        
        if symbol not in self.handlers:
            return
        
        handler = self.handlers[symbol]
        price = float(k.get('c', 0))
        
        # 只在K线收盘时处理信号
        if is_closed:
            kline = Kline(
                timestamp=k.get('t'),
                open=float(k.get('o')),
                high=float(k.get('h')),
                low=float(k.get('l')),
                close=float(k.get('c')),
                volume=float(k.get('v'))
            )
            
            signal = handler.on_kline_close(kline, price)
            
            if signal:
                self._print_signal(signal)
            
            # 定期保存模型
            now = datetime.now()
            if (now - self.last_save_time).total_seconds() >= MODEL_SAVE_INTERVAL * 60:
                self._save_models()
                self.last_save_time = now
        
        # 打印状态
        self._print_status(symbol, price, is_closed)
    
    def _print_status(self, symbol: str, price: float, is_closed: bool):
        """打印状态 - 两行显示BTC和ETH"""
        handler = self.handlers[symbol]
        feat = handler.last_features
        
        rsi6 = feat.get('rsi6', 50)
        bb_pct = feat.get('bb_pct', 0.5)
        
        # 统计
        win_rate = handler.strategy.win_rate
        total = handler.strategy.total_signals
        
        status = "收盘" if is_closed else "实时"
        now = datetime.now().strftime("%H:%M:%S")
        
        # 状态指示
        if rsi6 >= 70 and bb_pct >= 0.8:
            indicator = f"🔴超买 RSI={rsi6:.0f}"
        elif rsi6 <= 30 and bb_pct <= 0.2:
            indicator = f"🟢超卖 RSI={rsi6:.0f}"
        else:
            indicator = f"RSI={rsi6:.0f} BB={bb_pct:.2f}"
        
        # 胜率
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
        
        # 保存状态
        if not hasattr(self, '_lines'):
            self._lines = {}
        self._lines[symbol] = line
        
        # 两行显示
        btc = self._lines.get('BTCUSDT', 'BTCUSDT: 等待数据...')
        eth = self._lines.get('ETHUSDT', 'ETHUSDT: 等待数据...')
        
        # 移动光标到两行前，重新打印
        print(f"\033[2A\r{btc:<90}\n{eth:<90}", flush=True)
    
    def _print_signal(self, signal: Signal):
        """打印信号"""
        print()
        print("=" * 70)
        print(signal)
        print(f"  时间: {signal.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  详情: {signal.details}")
        print(f"  结算: {HORIZON}分钟后")
        print("=" * 70)
        print()

    async def _connect_ws(self):
        """连接WebSocket"""
        streams = "/".join([f"{s.lower()}@kline_1m" for s in self.symbols])
        url = f"wss://stream.binance.com:9443/stream?streams={streams}"
        
        print(f"连接 WebSocket: {url}")
        
        async for ws in websockets.connect(url, ping_interval=20):
            try:
                self.ws = ws
                print("✅ WebSocket 已连接\n")
                
                async for message in ws:
                    data = json.loads(message)
                    if 'data' in data:
                        self._on_kline_message(data['data'])
            except websockets.ConnectionClosed:
                print("\n⚠️ WebSocket 断开，重连中...")
                continue
            except Exception as e:
                print(f"\n❌ WebSocket 错误: {e}")
                await asyncio.sleep(5)
                continue
    
    async def run(self):
        """运行服务"""
        print("=" * 70)
        print("🚀 Event Signal 服务")
        print("=" * 70)
        print(f"  交易对: {', '.join(self.symbols)}")
        print(f"  预测周期: {HORIZON}分钟")
        print(f"  盈亏平衡胜率: {BREAKEVEN_WINRATE:.2%}")
        print(f"  策略: 超买做空 + 超卖做多 + River在线学习")
        print("=" * 70)
        
        await self._fetch_history()
        print("\n开始监听K线...")
        print()  # BTC行占位
        print()  # ETH行占位
        await self._connect_ws()
    
    async def stop(self):
        """停止服务"""
        self._save_models()
        if self.ws:
            await self.ws.close()
