"""策略引擎：把「Agent + 资产 + 工具」映射成 ALLOW / APPROVAL / DENY。

规则优先级（从高到低）：
    asset  > agent > env > global
同名范围内再按 priority 排序（数值越大越优先）；
同一层级出现 DENY 时，DENY 永远胜出。

如果没有任何规则命中，采用保守默认：
    只读工具 -> ALLOW，其它 -> APPROVAL。
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .. import db

SPECIFICITY = {"asset": 4, "agent": 3, "env": 2, "global": 1}
DEFAULT_READ_ACTION = "ALLOW"
DEFAULT_WRITE_ACTION = "APPROVAL"


def _applicable_policies(agent: Dict[str, Any], asset: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM policies WHERE enabled = 1")
    result = []
    for p in rows:
        scope, value = p["scope_type"], p["scope_value"]
        if scope == "global" and value in ("*", ""):
            result.append(p)
        elif scope == "env" and value == asset.get("env_tag"):
            result.append(p)
        elif scope == "asset" and value == asset.get("name"):
            result.append(p)
        elif scope == "agent" and value == agent.get("name"):
            result.append(p)
    return result


def decide(
    agent: Dict[str, Any],
    asset: Dict[str, Any],
    tool: Dict[str, Any],
) -> Dict[str, Any]:
    """返回 {action, policy_name, rule_reason, matched}。"""
    tool_name = tool["name"]
    category = tool.get("category", "read")
    default_action = DEFAULT_READ_ACTION if category == "read" else DEFAULT_WRITE_ACTION

    candidates: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    for policy in _applicable_policies(agent, asset):
        weight = SPECIFICITY.get(policy["scope_type"], 1) * 1000 + int(policy["priority"])
        rules = db.query("SELECT * FROM policy_rules WHERE policy_id = ?", (policy["id"],))
        for rule in rules:
            if rule["tool"] in (tool_name, "*"):
                # 精确工具名优先于通配
                exact_bonus = 5 if rule["tool"] == tool_name else 0
                candidates.append((weight + exact_bonus, policy, rule))

    if not candidates:
        return {
            "action": default_action,
            "policy_name": "",
            "reason": "未命中任何策略，按默认保守策略处理（%s）" % default_action,
            "matched": False,
        }

    denies = [c for c in candidates if c[2]["action"] == "DENY"]
    if denies:
        _, policy, rule = max(denies, key=lambda c: c[0])
        return {
            "action": "DENY",
            "policy_name": policy["name"],
            "reason": rule["reason"] or "命中禁止规则",
            "matched": True,
        }

    _, policy, rule = max(candidates, key=lambda c: c[0])
    return {
        "action": rule["action"],
        "policy_name": policy["name"],
        "reason": rule["reason"] or "命中策略规则",
        "matched": True,
    }


def list_policies() -> List[Dict[str, Any]]:
    policies = db.query("SELECT * FROM policies ORDER BY priority DESC, id ASC")
    for p in policies:
        p["rules"] = db.query(
            "SELECT * FROM policy_rules WHERE policy_id = ? ORDER BY id", (p["id"],)
        )
        p["enabled"] = bool(p["enabled"])
    return policies


def create_policy(payload: Dict[str, Any]) -> int:
    from ..util import now_iso

    policy_id = db.execute(
        """INSERT INTO policies (name, description, scope_type, scope_value, priority, enabled, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            payload["name"],
            payload.get("description", ""),
            payload.get("scope_type", "env"),
            payload.get("scope_value", "*"),
            int(payload.get("priority", 100)),
            1 if payload.get("enabled", True) else 0,
            now_iso(),
        ),
    )
    for rule in payload.get("rules", []):
        add_rule(policy_id, rule)
    return policy_id


def add_rule(policy_id: int, rule: Dict[str, Any]) -> int:
    return db.execute(
        """INSERT INTO policy_rules (policy_id, tool, action, reason, max_risk)
           VALUES (?, ?, ?, ?, ?)""",
        (
            policy_id,
            rule["tool"],
            rule["action"].upper(),
            rule.get("reason", ""),
            rule.get("max_risk", ""),
        ),
    )


def delete_rule(rule_id: int) -> None:
    db.execute("DELETE FROM policy_rules WHERE id = ?", (rule_id,))
