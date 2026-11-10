"""审计中心：会话检索、完整链路还原、导出、风险事件。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response

from .. import db
from ..audit import logger as audit
from .deps import current_user

router = APIRouter(prefix="/api/audit", tags=["审计"])


@router.get("/sessions")
def audit_sessions(
    user: Dict[str, Any] = Depends(current_user),
    keyword: str = "",
    risk_level: str = "",
    status: str = "",
    agent: str = "",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = """SELECT s.*, u.username, u.display_name AS user_name, ag.name AS agent_name,
                    ast.name AS asset_name, ast.env_tag,
                    (SELECT COUNT(*) FROM tool_calls t WHERE t.session_id = s.id) AS tool_count,
                    (SELECT COUNT(*) FROM audit_events e WHERE e.session_id = s.id) AS event_count,
                    (SELECT COUNT(*) FROM risk_events r WHERE r.session_id = s.id) AS risk_count,
                    (SELECT COUNT(*) FROM approvals p WHERE p.session_id = s.id AND p.status = 'approved') AS approved_count,
                    (SELECT COUNT(*) FROM approvals p WHERE p.session_id = s.id AND p.status = 'rejected') AS rejected_count
             FROM sessions s
             JOIN users u ON u.id = s.user_id
             JOIN agents ag ON ag.id = s.agent_id
             JOIN assets ast ON ast.id = s.asset_id
             WHERE 1 = 1"""
    params: List[Any] = []
    # 运维人员只能审计自己的会话；安全管理员与审计管理员可查看全部
    if user["role"] == "operator":
        sql += " AND s.user_id = ?"
        params.append(user["id"])
    if keyword:
        sql += " AND (s.code LIKE ? OR u.username LIKE ? OR s.title LIKE ? OR s.summary LIKE ?)"
        params += ["%%%s%%" % keyword] * 4
    if risk_level:
        sql += " AND s.risk_level = ?"
        params.append(risk_level)
    if status:
        sql += " AND s.status = ?"
        params.append(status)
    if agent:
        sql += " AND ag.name = ?"
        params.append(agent)
    sql += " ORDER BY s.id DESC LIMIT ?"
    params.append(int(limit))
    return db.query(sql, params)


@router.get("/sessions/{session_id}")
def audit_detail(session_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    session = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if user["role"] == "operator" and session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="只能查看自己发起的会话审计")
    bundle = audit.export_session(session_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    bundle["events"] = audit.list_events(session_id)
    return bundle


@router.get("/sessions/{session_id}/export")
def export_session(session_id: int, user: Dict[str, Any] = Depends(current_user),
                   format: str = "json") -> Response:
    owner = db.query_one("SELECT user_id FROM sessions WHERE id = ?", (session_id,))
    if owner is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if user["role"] == "operator" and owner["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="只能导出自己发起的会话审计")
    if format == "csv":
        csv_text = audit.export_session(session_id, fmt="csv")
        if csv_text is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        code = (db.query_one("SELECT code FROM sessions WHERE id = ?", (session_id,)) or {}).get("code", session_id)
        return Response(
            content="\ufeff" + csv_text,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="audit-%s.csv"' % code},
        )
    bundle = audit.export_session(session_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    import json

    code = bundle["session"]["code"]
    return Response(
        content=json.dumps(bundle, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="audit-%s.json"' % code},
    )


@router.get("/risk-events")
def risk_events(user: Dict[str, Any] = Depends(current_user), limit: int = 100,
                level: str = "") -> List[Dict[str, Any]]:
    sql = """SELECT r.*, s.code AS session_code, u.display_name AS user_name
             FROM risk_events r
             LEFT JOIN sessions s ON s.id = r.session_id
             LEFT JOIN users u ON u.id = s.user_id
             WHERE 1 = 1"""
    params: List[Any] = []
    if level:
        sql += " AND r.level = ?"
        params.append(level)
    sql += " ORDER BY r.id DESC LIMIT ?"
    params.append(int(limit))
    return db.query(sql, params)


@router.get("/events")
def recent_events(user: Dict[str, Any] = Depends(current_user), limit: int = 60) -> List[Dict[str, Any]]:
    rows = db.query(
        """SELECT e.*, s.code AS session_code FROM audit_events e
           LEFT JOIN sessions s ON s.id = e.session_id
           ORDER BY e.id DESC LIMIT ?""",
        (int(limit),),
    )
    for row in rows:
        row["label"] = audit.EVENT_LABELS.get(row["event_type"], row["event_type"])
    return rows
