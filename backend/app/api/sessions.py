"""会话与对话：创建会话、下发任务、查看实时过程、结束会话。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..ai import runner
from ..security import gateway
from ..util import loads
from .deps import current_user

router = APIRouter(prefix="/api/sessions", tags=["会话"])

# 角色矩阵：谁能创建会话 / 下达任务（审计管理员为只读角色）
CAN_OPERATE = ("operator", "security_admin")


def _session_row(session_id: int) -> Optional[Dict[str, Any]]:
    return db.query_one(
        """SELECT s.*, u.username, u.display_name AS user_name, ag.name AS agent_name,
                  ast.name AS asset_name, ast.env_tag, ast.executor
           FROM sessions s
           JOIN users u ON u.id = s.user_id
           JOIN agents ag ON ag.id = s.agent_id
           JOIN assets ast ON ast.id = s.asset_id
           WHERE s.id = ?""",
        (session_id,),
    )


class SessionCreate(BaseModel):
    agent_id: int
    asset_id: int
    title: str = "AI 运维会话"


class PromptRequest(BaseModel):
    prompt: str


@router.get("")
def list_sessions(user: Dict[str, Any] = Depends(current_user),
                  limit: int = 50, mine: bool = False) -> List[Dict[str, Any]]:
    sql = """SELECT s.*, u.username, u.display_name AS user_name, ag.name AS agent_name,
                    ast.name AS asset_name, ast.env_tag
             FROM sessions s
             JOIN users u ON u.id = s.user_id
             JOIN agents ag ON ag.id = s.agent_id
             JOIN assets ast ON ast.id = s.asset_id"""
    params: List[Any] = []
    if mine or user["role"] == "operator":
        sql += " WHERE s.user_id = ?"
        params.append(user["id"])
    sql += " ORDER BY s.id DESC LIMIT ?"
    params.append(int(limit))
    rows = db.query(sql, params)
    for row in rows:
        row["pending_approvals"] = int((db.query_one(
            "SELECT COUNT(*) AS n FROM approvals WHERE session_id = ? AND status = 'pending'", (row["id"],)
        ) or {}).get("n") or 0)
    return rows


@router.post("")
def create_session(payload: SessionCreate, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user["role"] not in CAN_OPERATE:
        raise HTTPException(status_code=403, detail="审计管理员为只读角色，不能创建 AI 运维会话")
    try:
        return runner.create_session(user, payload.agent_id, payload.asset_id, payload.title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{session_id}")
def get_session(session_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    session = _session_row(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = db.query("SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (session_id,))
    for message in messages:
        message["meta"] = loads(message.get("meta"), {}) or {}
    tool_calls = db.query("SELECT * FROM tool_calls WHERE session_id = ? ORDER BY seq", (session_id,))
    for call in tool_calls:
        call["arguments"] = loads(call.get("arguments"), {}) or {}
        call["risk_reasons"] = loads(call.get("risk_reasons"), []) or []
    approvals = db.query("SELECT * FROM approvals WHERE session_id = ? ORDER BY id", (session_id,))
    risk_events = db.query("SELECT * FROM risk_events WHERE session_id = ? ORDER BY id", (session_id,))
    session.update({
        "messages": messages,
        "tool_calls": tool_calls,
        "approvals": approvals,
        "risk_events": risk_events,
        "running": session_id in runner.ACTIVE_RUNS and not runner.ACTIVE_RUNS[session_id].done(),
    })
    return session


@router.post("/{session_id}/prompt")
async def send_prompt(session_id: int, payload: PromptRequest,
                      user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if user["role"] not in CAN_OPERATE:
        raise HTTPException(status_code=403, detail="审计管理员为只读角色，不能下达 AI 任务")
    session = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if user["role"] == "operator" and session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="只能在自己创建的会话中下达任务")
    if session["status"] in ("finished", "terminated"):
        raise HTTPException(status_code=400, detail="会话已结束，请创建新会话")
    if not payload.prompt.strip():
        raise HTTPException(status_code=400, detail="任务内容不能为空")
    try:
        runner.spawn_turn(session_id, payload.prompt.strip())
    except runner.SessionBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"accepted": True, "session_id": session_id}


@router.post("/{session_id}/finish")
def finish_session(session_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    runner.finish_session(session_id, user["username"])
    return {"ok": True}


@router.delete("/{session_id}")
def terminate_session(session_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """安全管理员强制终止会话（原则五：临时权限立即失效）。"""
    if user["role"] not in ("security_admin", "operator"):
        raise HTTPException(status_code=403, detail="无权终止会话")
    session = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if user["role"] == "operator" and session["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="只能终止自己的会话")
    runner.finish_session(session_id, user["username"], terminated=True)
    task = runner.ACTIVE_RUNS.get(session_id)
    if task is not None and not task.done():
        task.cancel()
    return {"ok": True}


@router.post("/{session_id}/preview")
def preview_authorization(session_id: int, payload: Dict[str, Any],
                          user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """策略预演：在真正执行前，看看某个工具会被判定为放行 / 审批 / 拒绝。"""
    session = _session_row(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    from ..ai import tools as tool_registry

    tool = tool_registry.get_tool(payload.get("tool", ""))
    if not tool:
        raise HTTPException(status_code=404, detail="工具不存在")
    agent = db.query_one("SELECT * FROM agents WHERE id = ?", (session["agent_id"],)) or {}
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (session["asset_id"],)) or {}
    owner = db.query_one("SELECT * FROM users WHERE id = ?", (session["user_id"],)) or {}
    arguments = tool_registry.validate_arguments(tool, payload.get("arguments") or {})
    result = gateway.authorize(owner, agent, asset, tool, arguments)
    result["command"] = tool_registry.build_command(tool, arguments)
    return result
