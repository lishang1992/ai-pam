"""演示辅助接口：一键重置演示环境，方便反复演示同一个故事。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from .. import db
from ..audit import logger as audit
from ..proxy import mock_linux
from .deps import current_user, require_role

router = APIRouter(prefix="/api/demo", tags=["演示"])


@router.post("/reset")
def reset(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """把模拟主机的故障场景恢复到初始状态，并可选清空历史会话。"""
    require_role(user, ["security_admin", "operator", "auditor"])
    mock_linux.reset_all()
    audit.record("demo_reset", actor=user["username"], payload={"scope": "mock-hosts"})
    return {"ok": True, "message": "演示资产状态已恢复到初始故障场景"}


@router.post("/clear-sessions")
def clear_sessions(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    db.execute("DELETE FROM sessions")
    db.execute("DELETE FROM audit_events WHERE session_id IS NOT NULL")
    audit.record("demo_reset", actor=user["username"], payload={"scope": "sessions"})
    return {"ok": True}

