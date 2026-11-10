"""全局配置。所有可调参数集中在这里，便于演示前现场修改。"""

from __future__ import annotations

import os
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---------------------------------------------------------------- 路径

BACKEND_DIR = Path(__file__).resolve().parent.parent          # backend/
PROJECT_DIR = BACKEND_DIR.parent                               # 项目根目录
CONFIG_DIR = Path(os.getenv("AIB_CONFIG_DIR", BACKEND_DIR / "config"))
FRONTEND_DIR = Path(os.getenv("AIB_FRONTEND_DIR", PROJECT_DIR / "frontend"))
DATA_DIR = Path(os.getenv("AIB_DATA_DIR", PROJECT_DIR / "data"))
AUDIT_DIR = DATA_DIR / "audit"

DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("AIB_DB_PATH", DATA_DIR / "bastion.db"))

# ---------------------------------------------------------------- 账号 / 会话

SECRET_KEY = os.getenv("AIB_SECRET_KEY", "aibastion-dev-secret-change-me")
TOKEN_TTL_SECONDS = _env_int("AIB_TOKEN_TTL", 8 * 3600)

# ---------------------------------------------------------------- 演示开关

# 演示模式：默认开启规则化 Planner（离线可用，不依赖大模型 API）。
# 配置了 AIB_LLM_API_KEY 后，把该开关设为 0 即可切换到真实 LLM。
DEMO_MODE = _env_bool("AIB_DEMO_MODE", True)

# ---------------------------------------------------------------- 大模型

LLM_BASE_URL = os.getenv("AIB_LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("AIB_LLM_API_KEY", "")
LLM_MODEL = os.getenv("AIB_LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT = _env_int("AIB_LLM_TIMEOUT", 60)
LLM_TEMPERATURE = float(os.getenv("AIB_LLM_TEMPERATURE", "0.2"))


def llm_enabled() -> bool:
    """是否启用真实大模型。演示模式下永远走本地 Planner。"""
    return bool(LLM_API_KEY) and not DEMO_MODE


# ---------------------------------------------------------------- 安全策略

# 风险等级达到该阈值时，即使策略是 ALLOW，也会被升级为「需要人工审批」。
RISK_ESCALATE_TO_APPROVAL_AT = os.getenv("AIB_RISK_ESCALATE_AT", "HIGH")

# 风险等级达到该阈值时，直接阻断，不允许任何审批绕过。
RISK_BLOCK_AT = os.getenv("AIB_RISK_BLOCK_AT", "CRITICAL")

# 人工审批超时时间（秒）。超时视为拒绝。
APPROVAL_TIMEOUT_SECONDS = _env_int("AIB_APPROVAL_TIMEOUT", 900)

# 单次会话最多允许的 AI 工具调用步数，防止 Agent 死循环。
MAX_TOOL_STEPS = _env_int("AIB_MAX_TOOL_STEPS", 12)

# 审计 JSONL 落盘开关。
AUDIT_TO_DISK = _env_bool("AIB_AUDIT_TO_DISK", True)

# ---------------------------------------------------------------- SSH

SSH_CONNECT_TIMEOUT = _env_int("AIB_SSH_CONNECT_TIMEOUT", 10)
SSH_COMMAND_TIMEOUT = _env_int("AIB_SSH_COMMAND_TIMEOUT", 30)

