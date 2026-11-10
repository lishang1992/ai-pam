"""Tool Gateway —— AI 与目标主机之间的唯一通道，整个产品的安全咽喉。

一次工具调用必须依次通过：
    身份权限校验 → 策略引擎 → 风险引擎 →（必要时）人工审批 → 执行代理
任何一步不通过，AI 都拿不到执行权。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .. import db
from ..ai import tools as tool_registry
from ..proxy import ssh_proxy
from ..settings import RISK_BLOCK_AT, RISK_ESCALATE_TO_APPROVAL_AT
from . import policy, risk


def _user_asset_granted(user_id: int, asset_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM user_assets WHERE user_id = ? AND asset_id = ?", (user_id, asset_id)
    ) is not None


def _user_agent_granted(user_id: int, agent_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM user_agents WHERE user_id = ? AND agent_id = ?", (user_id, agent_id)
    ) is not None


def _agent_asset_granted(agent_id: int, asset_id: int) -> bool:
    return db.query_one(
        "SELECT 1 FROM agent_assets WHERE agent_id = ? AND asset_id = ?", (agent_id, asset_id)
    ) is not None


def _agent_tool_granted(agent_id: int, tool_name: str) -> bool:
    return db.query_one(
        "SELECT 1 FROM agent_tools WHERE agent_id = ? AND tool_name = ?", (agent_id, tool_name)
    ) is not None


def authorize(
    user: Dict[str, Any],
    agent: Dict[str, Any],
    asset: Dict[str, Any],
    tool: Dict[str, Any],
    arguments: Dict[str, Any],
) -> Dict[str, Any]:
    """返回完整判定结果，不产生任何副作用。"""
    checks: List[Tuple[str, bool, str]] = [
        ("agent_enabled", agent.get("status") == "enabled", "Agent 必须处于启用状态"),
        ("user_agent_grant", _user_agent_granted(int(user["id"]), int(agent["id"])),
         "需要用户 %s 被授权使用 Agent %s" % (user["username"], agent["name"])),
        ("agent_tool_grant", _agent_tool_granted(int(agent["id"]), tool["name"]),
         "需要工具 %s 在 Agent 白名单内" % tool["name"]),
        ("agent_asset_grant", _agent_asset_granted(int(agent["id"]), int(asset["id"])),
         "需要资产 %s 已授权给 Agent" % asset["name"]),
        # 原则二：Agent 权限不能超过用户权限
        ("user_asset_grant", _user_asset_granted(int(user["id"]), int(asset["id"])),
         "需要用户本人拥有资产 %s 的访问权限" % asset["name"]),
    ]

    failed = [(name, note) for name, ok, note in checks if not ok]
    if failed:
        return {
            "allowed": False,
            "decision": "DENY",
            "source": "permission",
            "risk_level": "LOW",
            "risk_reasons": ["权限校验不通过：" + "；".join(note for _, note in failed)],
            "policy_name": "身份与权限校验",
            "policy_reason": "Agent 权限不能超过用户权限，任一校验失败即拒绝执行。失败项：%s"
                             % "；".join(note for _, note in failed),
            "failed_checks": [name for name, _ in failed],
            "checks": [{"name": n, "ok": ok, "note": note} for n, ok, note in checks],
        }

    decision = policy.decide(agent, asset, tool)
    level, reasons = risk.evaluate_tool(tool, arguments, asset)

    action = decision["action"]
    policy_reason = decision["reason"]
    source = "policy"

    # 风险升级：策略允许，但风险过高时仍然要人审
    if action == "ALLOW" and risk.at_least(level, RISK_ESCALATE_TO_APPROVAL_AT):
        action = "APPROVAL"
        source = "risk_escalation"
        policy_reason = "策略结果为放行，但风险等级 %s 达到人工审批阈值，自动升级为审批" % level

    # 风险阻断：达到阻断阈值，任何审批都不能绕过
    if risk.at_least(level, RISK_BLOCK_AT):
        action = "DENY"
        source = "risk_block"
        policy_reason = "风险等级 %s 达到阻断阈值，禁止任何形式的执行" % level

    return {
        "allowed": action != "DENY",
        "decision": action,
        "source": source,
        "risk_level": level,
        "risk_reasons": reasons,
        "policy_name": decision["policy_name"] or "默认策略",
        "policy_reason": policy_reason,
        "failed_checks": [],
        "checks": [{"name": n, "ok": ok, "note": note} for n, ok, note in checks],
    }


def execute(
    asset: Dict[str, Any],
    tool: Dict[str, Any],
    arguments: Dict[str, Any],
    command: str,
) -> Dict[str, Any]:
    """真正落到目标主机。只有 authorize 通过（含审批通过）才会走到这里。"""
    ssh_proxy.check_command_safety(command)
    return ssh_proxy.execute(asset, tool, arguments, command)


def preview(tool: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, str]:
    return {"command": tool_registry.build_command(tool, arguments)}
