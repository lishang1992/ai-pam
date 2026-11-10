#!/usr/bin/env bash
# AI 安全堡垒机 V1 —— macOS / Linux 一键启动
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "[1/3] 创建虚拟环境 .venv"
  python3 -m venv .venv
fi

echo "[2/3] 安装依赖"
./.venv/bin/python -m pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt

echo "[3/3] 启动服务 http://127.0.0.1:8000"
exec ./.venv/bin/python run.py "$@"

