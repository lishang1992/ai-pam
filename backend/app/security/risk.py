"""风险引擎：给每个 Tool Call 和每条 Prompt 打分，并给出可读的风险原因。

设计原则：
  * 风险判定不依赖大模型，规则化、可解释、可审计；
  * 大模型只能「建议」，最终等级由本模块给出，杜绝 AI 自证清白。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ..settings import RISK_ESCALATE_TO_APPROVAL_AT

LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
LEVEL_ZH = {
    "LOW": "低风险",
    "MEDIUM": "中风险",
    "HIGH": "高风险",
    "CRITICAL": "严重风险",
}


def rank(level: str) -> int:
    try:
        return LEVELS.index((level or "LOW").upper())
    except ValueError:
        return 0


def max_level(*levels: str) -> str:
    best = "LOW"
    for lv in levels:
        if rank(lv) > rank(best):
            best = (lv or "LOW").upper()
    return best


def at_least(level: str, threshold: str) -> bool:
    return rank(level) >= rank(threshold)


# ------------------------------------------------------------------ Tool 风险

# 参数层面的危险特征（真正决定「同工具不同参数不同风险」）
_ARGS_RULES: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"(^|/)etc/(passwd|shadow|sudoers)"), "HIGH", "涉及系统敏感配置文件"),
    (re.compile(r"(^|/)\.ssh/|id_rsa|id_ed25519"), "CRITICAL", "涉及 SSH 私钥/凭据文件"),
    (re.compile(r"rm\s+-rf|rm\s+-fr"), "CRITICAL", "包含递归强制删除命令"),
    (re.compile(r"drop\s+table|truncate\s+table", re.I), "CRITICAL", "包含数据库破坏性语句"),
    (re.compile(r"\bsudo\b|\bsu\s+-"), "HIGH", "包含提权动作"),
    (re.compile(r"chmod\s+777|chown\s+root", re.I), "HIGH", "包含危险的权限变更"),
    (re.compile(r"/var/lib/mysql|/etc/mysql|/etc/nginx|/etc/systemd"), "HIGH", "涉及生产服务关键路径"),
]

# 高危服务（重启/停止这类服务影响面大）
_CRITICAL_SERVICES = {
    "mysql": "核心数据库服务",
    "mysqld": "核心数据库服务",
    "mariadb": "核心数据库服务",
    "postgresql": "核心数据库服务",
    "postgres": "核心数据库服务",
    "redis": "核心缓存服务",
    "kafka": "核心消息中间件",
    "docker": "容器运行时",
    "sshd": "SSH 接入服务",
    "network": "网络服务",
}


def evaluate_tool(
    tool: Dict[str, Any],
    arguments: Dict[str, Any],
    asset: Dict[str, Any],
) -> Tuple[str, List[str]]:
    """返回 (风险等级, 原因列表)。"""
    level = (tool.get("risk_hint") or "LOW").upper()
    category = tool.get("category") or "read"
    env_tag = (asset or {}).get("env_tag", "test")
    reasons: List[str] = []

    if category == "read":
        reasons.append("只读查询类操作，不改变系统状态")
    elif category == "write":
        reasons.append("修改类操作，会改变服务或系统状态")
    elif category == "destructive":
        reasons.append("破坏性操作，可能造成服务中断或数据丢失")
    elif category == "data":
        reasons.append("涉及数据读取/导出，存在数据泄露风险")

    if env_tag == "production" and category != "read":
        level = max_level(level, "HIGH")
        reasons.append("目标资产属于生产环境，非只读操作强制升级为高风险")
    if env_tag == "production" and category == "read":
        reasons.append("目标资产属于生产环境，读取操作留痕审计")

    flat = " ".join(str(v) for v in (arguments or {}).values())
    for pattern, rule_level, note in _ARGS_RULES:
        if pattern.search(flat):
            level = max_level(level, rule_level)
            reasons.append(note)

    service = str((arguments or {}).get("service", "")).lower()
    if service in _CRITICAL_SERVICES and category != "read":
        level = max_level(level, "CRITICAL")
        reasons.append("目标服务 %s 属于%s，禁止由 AI 直接变更" % (service, _CRITICAL_SERVICES[service]))

    if tool.get("name") in {"delete_file", "drop_database", "export_database"}:
        level = max_level(level, "CRITICAL")

    return level, reasons


# ------------------------------------------------------------------ Prompt 风险

_PROMPT_RULES: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r"/etc/(passwd|shadow)|shadow\s*文件|passwd\s*文件", re.I), "CRITICAL", "试图读取系统账号敏感文件"),
    (re.compile(r"私钥|id_rsa|\.ssh|ssh\s*key|密钥对", re.I), "CRITICAL", "试图获取 SSH 私钥/密钥材料"),
    (re.compile(r"(导出|下载|打包|发给我|发送).{0,12}(数据库|全库|全部数据|所有数据|用户表)", re.I), "CRITICAL", "试图批量导出生产数据"),
    (re.compile(r"(数据库|全库|全部数据|所有数据|用户表|生产数据).{0,12}(导出|下载|打包|发给我|发送|外发)", re.I), "CRITICAL", "试图批量导出生产数据"),
    (re.compile(r"全量\s*(导出|备份|dump)|导出\s*(全库|全量|所有数据)", re.I), "CRITICAL", "试图批量导出生产数据"),
    (re.compile(r"\bdump\b|mysqldump|pg_dump", re.I), "HIGH", "涉及数据库导出工具"),
    (re.compile(r"(删除|清空|清理).{0,10}(日志|审计|记录)", re.I), "CRITICAL", "试图删除审计日志"),
    (re.compile(r"(绕过|跳过|规避|关闭).{0,10}(审批|策略|审计|风控|权限)", re.I), "CRITICAL", "试图绕过安全控制"),
    (re.compile(r"(提权|sudo\s+su|root\s*密码|获取\s*root)", re.I), "HIGH", "试图提升权限"),
    (re.compile(r"(密码|口令|token|api\s*key|access\s*key)\s*(是多少|发给我|告诉我|是什么)", re.I), "HIGH", "试图套取账号凭据"),
    (re.compile(r"chmod\s+777|chown\s+root", re.I), "HIGH", "试图修改关键权限"),
    (re.compile(r"drop\s+table|delete\s+from|truncate", re.I), "HIGH", "包含破坏性数据操作意图"),
]


def scan_prompt(text: str) -> Tuple[str, List[str]]:
    """对用户 Prompt 做安全检测，返回 (风险等级, 原因列表)。"""
    level = "LOW"
    reasons: List[str] = []
    for pattern, rule_level, note in _PROMPT_RULES:
        if pattern.search(text or "") and note not in reasons:
            level = max_level(level, rule_level)
            reasons.append(note)
    return level, reasons


def escalate_threshold(level: str) -> bool:
    return at_least(level, RISK_ESCALATE_TO_APPROVAL_AT)
