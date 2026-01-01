#!/usr/bin/env python3
"""
启动 Event Signal 服务
预测10分钟后价格涨跌，胜率 > 55.56% 才能盈利
"""
import asyncio
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from event_signal.service import EventSignalService


def main():
    parser = argparse.ArgumentParser(description="Event Signal 预测服务")
    parser.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT",
                        help="交易对，逗号分隔")
    parser.add_argument("--no-pretrain", action="store_true",
                        help="不加载预训练模型")
    args = parser.parse_args()
    
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    service = EventSignalService(
        symbols=symbols,
        load_pretrained=not args.no_pretrain
    )
    
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        print("\n\n已停止")


if __name__ == "__main__":
    main()
