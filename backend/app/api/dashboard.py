"""安全态势看板（按角色呈现不同的作战面）。

  * 运维人员：只看自己的会话、自己的待审批、自己触发的高风险操作
  * 安全管理员：全局安全态势 + 全部待审批（可直接在页面审批）
  * 审计管理员：全局统计 + 风险事件，用于出审计结论
"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from .. import db
from ..settings import DEMO_MODE, llm_enabled
from ..util import loads, now_iso
from .deps import current_user

router = APIRouter(prefix="/api/dashboard", tags=["看板"])

ROLE_NAME = {"operator": "运维人员", "security_admin": "安全管理员", "auditor": "审计管理员"}


@router.get("/summary")
def summary(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    today = now_iso()[:10]
    # 运维人员只看自己的数据，其余角色看全局
    mine = user["role"] == "operator"
    scope = " AND s.user_id = ?" if mine else ""
    scope_params: List[Any] = [user["id"]] if mine else []

    def count(sql: str, params: List[Any] = None) -> int:
        return int((db.query_one(sql, params if params is not None else []) or {}).get("n") or 0)

    total_today = count(
        "SELECT COUNT(*) AS n FROM sessions s WHERE substr(s.created_at, 1, 10) = ?" + scope,
        [today] + scope_params,
    )
    pending = count(
        """SELECT COUNT(*) AS n FROM approvals a JOIN sessions s ON s.id = a.session_id
           WHERE a.status = 'pending'""" + scope, scope_params)
    high_risk = count(
        """SELECT COUNT(*) AS n FROM risk_events r LEFT JOIN sessions s ON s.id = r.session_id
           WHERE r.level IN ('HIGH', 'CRITICAL')""" + scope, scope_params)
    blocked = count(
        """SELECT COUNT(*) AS n FROM tool_calls t JOIN sessions s ON s.id = t.session_id
           WHERE t.status IN ('blocked', 'denied')""" + scope, scope_params)
    prompt_blocked = count(
        """SELECT COUNT(*) AS n FROM audit_events e LEFT JOIN sessions s ON s.id = e.session_id
           WHERE e.event_type = 'prompt_blocked'""" + scope, scope_params)
    executed = count(
        """SELECT COUNT(*) AS n FROM tool_calls t JOIN sessions s ON s.id = t.session_id
           WHERE t.status = 'success'""" + scope, scope_params)
    approved = count(
        """SELECT COUNT(*) AS n FROM approvals a JOIN sessions s ON s.id = a.session_id
           WHERE a.status = 'approved'""" + scope, scope_params)
    rejected = count(
        """SELECT COUNT(*) AS n FROM approvals a JOIN sessions s ON s.id = a.session_id
           WHERE a.status = 'rejected'""" + scope, scope_params)
    sessions_total = count("SELECT COUNT(*) AS n FROM sessions s WHERE 1 = 1" + scope, scope_params)
    assets_total = count("SELECT COUNT(*) AS n FROM assets") if not mine else count(
        "SELECT COUNT(*) AS n FROM user_assets WHERE user_id = ?", [user["id"]])
    agents_total = count("SELECT COUNT(*) AS n FROM agents")

    recent_risks = db.query(
        """SELECT r.*, s.code AS session_code, u.display_name AS user_name
           FROM risk_events r
           LEFT JOIN sessions s ON s.id = r.session_id
           LEFT JOIN users u ON u.id = s.user_id
           WHERE 1 = 1""" + scope + " ORDER BY r.id DESC LIMIT 8",
        scope_params,
    )
    recent_sessions = db.query(
        """SELECT s.*, u.display_name AS user_name, ag.name AS agent_name, ast.name AS asset_name
           FROM sessions s JOIN users u ON u.id = s.user_id
           JOIN agents ag ON ag.id = s.agent_id JOIN assets ast ON ast.id = s.asset_id
           WHERE 1 = 1""" + scope + " ORDER BY s.id DESC LIMIT 6",
        scope_params,
    )
    by_level = {
        row["level"]: row["n"] for row in db.query(
            """SELECT r.level AS level, COUNT(*) AS n FROM risk_events r
               LEFT JOIN sessions s ON s.id = r.session_id
               WHERE 1 = 1""" + scope + " GROUP BY r.level",
            scope_params,
        )
    }
    return {
        "date": today,
        "role": user["role"],
        "role_name": ROLE_NAME.get(user["role"], user["role"]),
        "scope": "mine" if mine else "all",
        "cards": {
            "sessions_today": total_today,
            "sessions_total": sessions_total,
            "pending_approvals": pending,
            "high_risk": high_risk,
            "blocked": blocked + prompt_blocked,
            "executed": executed,
            "approved": approved,
            "rejected": rejected,
            "assets": assets_total,
            "agents": agents_total,
        },
        "risk_by_level": by_level,
        "pending_approvals": pending_approvals(user),
        "recent_risks": recent_risks,
        "recent_sessions": recent_sessions,
        "runtime": {
            "demo_mode": DEMO_MODE,
            "llm_enabled": llm_enabled(),
            "policy_engine": "builtin",
        },
    }


def pending_approvals(user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """待审批清单：运维人员看自己会话的，安全管理员/审计员看全部。"""
    sql = """SELECT a.id, a.tool_call_id, a.requested_at, a.expires_at,
                    s.id AS session_id, s.code AS session_code, s.title,
                    t.tool, t.arguments, t.risk_level, t.risk_reasons, t.command, t.reason,
                    u.display_name AS user_name, ast.name AS asset_name
             FROM approvals a
             JOIN sessions s ON s.id = a.session_id
             JOIN tool_calls t ON t.id = a.tool_call_id
             JOIN users u ON u.id = s.user_id
             JOIN assets ast ON ast.id = s.asset_id
             WHERE a.status = 'pending'"""
    params: List[Any] = []
    if user["role"] == "operator":
        sql += " AND s.user_id = ?"
        params.append(user["id"])
    sql += " ORDER BY a.id DESC LIMIT 10"
    rows = db.query(sql, params)
    for row in rows:
        row["arguments"] = loads(row.get("arguments"), {}) or {}
        row["risk_reasons"] = loads(row.get("risk_reasons"), []) or []
    return rows

