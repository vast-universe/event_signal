#!/usr/bin/env python3
"""
运行服务器
"""
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(project_root / ".env")

from event_signal.server import run_server

if __name__ == "__main__":
    run_server()
