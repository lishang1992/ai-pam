"""人工审批：待审批列表 + 审批决策。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..security import approvals as approval_center
from .deps import current_user

router = APIRouter(prefix="/api/approvals", tags=["审批"])


@router.get("")
def list_approvals(user: Dict[str, Any] = Depends(current_user),
                   status: str = "pending") -> List[Dict[str, Any]]:
    if status == "pending":
        return approval_center.list_pending()
    return db.query(
        """SELECT a.*, s.code AS session_code, t.tool, t.arguments, t.risk_level, t.risk_reasons
           FROM approvals a JOIN sessions s ON s.id = a.session_id
           JOIN tool_calls t ON t.id = a.tool_call_id
           WHERE a.status = ? ORDER BY a.id DESC LIMIT 200""",
        (status,),
    )


class ApprovalDecision(BaseModel):
    decision: str          # approved | rejected
    comment: str = ""


@router.post("/{tool_call_id}/decision")
def decide(tool_call_id: int, payload: ApprovalDecision,
           user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision 只能是 approved 或 rejected")
    row = db.query_one(
        "SELECT * FROM approvals WHERE tool_call_id = ? AND status = 'pending' ORDER BY id DESC LIMIT 1",
        (tool_call_id,),
    )
    if row is None:
        raise HTTPException(status_code=404, detail="该审批请求不存在或已处理")
    session = db.query_one("SELECT * FROM sessions WHERE id = ?", (row["session_id"],))
    # 审批人规则：会话发起人本人，或安全管理员
    if session and session["user_id"] != user["id"] and user["role"] != "security_admin":
        raise HTTPException(status_code=403, detail="只有会话发起人或安全管理员可以审批")
    updated = approval_center.resolve(tool_call_id, payload.decision, user["display_name"], payload.comment)
    return {"ok": True, "approval": updated}

