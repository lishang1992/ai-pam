"""AI 安全堡垒机 V1 —— 一键启动脚本。

用法：
    python run.py                       # 默认 0.0.0.0:8000
    python run.py --port 9000
    python run.py --host 127.0.0.1

Windows 上双击 start.bat 也可以。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 安全堡垒机 V1")
    parser.add_argument("--host", default=os.getenv("AIB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("AIB_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="开发模式：代码改动自动重启")
    args = parser.parse_args()

    try:
        import uvicorn  # noqa: F401
    except ImportError:
        print("缺少依赖，请先执行： pip install -r requirements.txt")
        sys.exit(1)

    import uvicorn

    print("=" * 68)
    print(" AI 安全堡垒机 V1  (AIBastion)")
    print(" 控制台地址： http://127.0.0.1:%d" % args.port)
    print(" 默认账号： zhangsan / ops123456  （运维人员）")
    print("           secadmin / sec123456  （安全管理员）")
    print("           auditor  / audit123456（审计管理员）")
    print("=" * 68)
    uvicorn.run("app.main:app", host=args.host, port=args.port, reload=args.reload,
                app_dir=str(ROOT / "backend"))


if __name__ == "__main__":
    main()

