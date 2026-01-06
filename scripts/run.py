#!/usr/bin/env python3
"""
运行服务器

使用方式:
  # 默认：从本地data目录训练 + API补齐（与回测一致）
  python run.py
  
  # 指定数据目录
  python run.py --data-dir /path/to/data

环境变量:
  DATA_DIR=/path/to/data  数据目录路径
"""
import argparse
import os
import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

# 加载 .env 文件
from dotenv import load_dotenv
load_dotenv(project_root / ".env")


def main():
    parser = argparse.ArgumentParser(description="Event Signal 服务器")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="数据目录路径")
    args = parser.parse_args()
    
    if args.data_dir:
        os.environ["DATA_DIR"] = args.data_dir
    else:
        # 默认数据目录：event_signal/data/
        default_data_dir = project_root / "data"
        os.environ["DATA_DIR"] = str(default_data_dir)
    
    from event_signal.server import run_server
    run_server()


if __name__ == "__main__":
    main()
