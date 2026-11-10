"""全链路审计日志。

同时写三个地方：
  1. audit_events 表 —— 支撑审计中心的查询与展示；
  2. data/audit/<session_code>.jsonl —— 每条会话一份不可篡改的原始流水；
  3. risk_events 表 —— 风险事件单独落表，便于统计「风险阻断」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import db
from ..settings import AUDIT_DIR, AUDIT_TO_DISK
from ..util import dumps, loads, now_iso


def _next_seq(session_id: Optional[int]) -> int:
    if session_id is None:
        return 0
    row = db.query_one(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM audit_events WHERE session_id = ?",
        (session_id,),
    )
    return int(row["seq"] if row else 0) + 1


def record(
    event_type: str,
    session_id: Optional[int] = None,
    actor: str = "system",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    seq = _next_seq(session_id)
    ts = now_iso()
    payload = payload or {}
    db.execute(
        """INSERT INTO audit_events (session_id, seq, ts, actor, event_type, payload)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, seq, ts, actor, event_type, dumps(payload)),
    )
    event = {
        "seq": seq,
        "ts": ts,
        "session_id": session_id,
        "actor": actor,
        "event_type": event_type,
        "payload": payload,
    }
    _append_disk(session_id, event)
    return event


def risk_event(
    level: str,
    category: str,
    detail: str,
    session_id: Optional[int] = None,
    tool_call_id: Optional[int] = None,
) -> None:
    db.execute(
        """INSERT INTO risk_events (session_id, tool_call_id, level, category, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, tool_call_id, level, category, detail, now_iso()),
    )
    record(
        "risk_event",
        session_id=session_id,
        actor="risk-engine",
        payload={"level": level, "category": category, "detail": detail,
                 "tool_call_id": tool_call_id},
    )


def _session_code(session_id: Optional[int]) -> str:
    if session_id is None:
        return "system"
    row = db.query_one("SELECT code FROM sessions WHERE id = ?", (session_id,))
    return (row or {}).get("code", "AI-unknown")


def _append_disk(session_id: Optional[int], event: Dict[str, Any]) -> None:
    if not AUDIT_TO_DISK:
        return
    code = _session_code(session_id)
    path = Path(AUDIT_DIR) / ("%s.jsonl" % code)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


# ------------------------------------------------------------------ 查询 / 导出

EVENT_LABELS = {
    "session_created": "创建会话",
    "prompt_received": "用户任务",
    "prompt_blocked": "任务被风控阻断",
    "ai_analysis": "AI 分析",
    "ai_tool_request": "AI 请求调用工具",
    "policy_decision": "策略判定",
    "risk_assessment": "风险评估",
    "approval_requested": "发起人工审批",
    "approval_decided": "人工审批结果",
    "tool_executed": "工具执行完成",
    "tool_failed": "工具执行失败",
    "tool_denied": "工具被拒绝",
    "session_finished": "会话结束",
    "session_terminated": "会话被强制终止",
    "asset_updated": "资产管理变更",
    "demo_reset": "演示数据重置",
}


def list_events(session_id: int) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM audit_events WHERE session_id = ? ORDER BY seq ASC", (session_id,)
    )
    for row in rows:
        row["payload"] = loads(row.get("payload"), {}) or {}
        row["label"] = EVENT_LABELS.get(row["event_type"], row["event_type"])
    return rows


def export_session(session_id: int, fmt: str = "json") -> Any:
    """导出单条会话的完整审计包（含 Prompt / Tool / 风险 / 审批 / 结果）。"""
    session = db.query_one(
        """SELECT s.*, u.username, u.display_name AS user_name, a.name AS agent_name,
                  ast.name AS asset_name, ast.env_tag
           FROM sessions s
           JOIN users u ON u.id = s.user_id
           JOIN agents a ON a.id = s.agent_id
           JOIN assets ast ON ast.id = s.asset_id
           WHERE s.id = ?""",
        (session_id,),
    )
    if session is None:
        return None
    bundle = {
        "exported_at": now_iso(),
        "session": session,
        "events": list_events(session_id),
        "messages": [
            dict(r)
            for r in db.query("SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (session_id,))
        ],
        "tool_calls": [
            dict(r)
            for r in db.query("SELECT * FROM tool_calls WHERE session_id = ? ORDER BY seq", (session_id,))
        ],
        "approvals": [
            dict(r)
            for r in db.query("SELECT * FROM approvals WHERE session_id = ? ORDER BY id", (session_id,))
        ],
        "risk_events": [
            dict(r)
            for r in db.query("SELECT * FROM risk_events WHERE session_id = ? ORDER BY id", (session_id,))
        ],
    }
    for key in ("messages", "tool_calls"):
        for item in bundle[key]:
            for field in ("meta", "arguments", "risk_reasons"):
                if field in item and isinstance(item[field], str):
                    item[field] = loads(item[field], item[field])
    if fmt == "csv":
        return _to_csv(bundle)
    return bundle


def _to_csv(bundle: Dict[str, Any]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["序号", "时间", "事件类型", "事件说明", "操作者", "详情"])
    for event in bundle["events"]:
        writer.writerow([
            event["seq"], event["ts"], event["event_type"], event.get("label", ""),
            event["actor"], json.dumps(event["payload"], ensure_ascii=False),
        ])
    writer.writerow([])
    writer.writerow(["工具调用明细"])
    writer.writerow(["序号", "工具", "参数", "风险", "策略判定", "状态", "结果"])
    for call in bundle["tool_calls"]:
        writer.writerow([
            call["seq"], call["tool"], json.dumps(call["arguments"], ensure_ascii=False),
            call["risk_level"], call["decision"], call["status"], call["result"][:500],
        ])
    return buf.getvalue()

