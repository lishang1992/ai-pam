"""人工审批中心。

实现要点：
  * 审批请求落库（approvals 表），重启后仍可查；
  * Agent 执行线程 await 一个 asyncio.Future，审批通过后立即续跑；
  * 超时按「拒绝」处理，并且会在审计里留下 expired 记录。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Dict, Optional

from .. import db
from ..settings import APPROVAL_TIMEOUT_SECONDS
from ..util import now_dt, now_iso

_waiters: Dict[int, asyncio.Future] = {}


def create_request(
    session_id: int,
    tool_call_id: int,
    requested_by: str = "ai-agent",
) -> Dict[str, Any]:
    expires = now_dt() + timedelta(seconds=APPROVAL_TIMEOUT_SECONDS)
    approved_id = db.execute(
        """INSERT INTO approvals (session_id, tool_call_id, status, requested_by, requested_at, expires_at)
           VALUES (?, ?, 'pending', ?, ?, ?)""",
        (session_id, tool_call_id, requested_by, now_iso(), expires.replace(microsecond=0).isoformat()),
    )
    return db.query_one("SELECT * FROM approvals WHERE id = ?", (approved_id,)) or {}


def register_waiter(tool_call_id: int) -> asyncio.Future:
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _waiters[tool_call_id] = fut
    return fut


async def wait_for_decision(tool_call_id: int, timeout: Optional[int] = None) -> str:
    fut = _waiters.get(tool_call_id)
    if fut is None:
        fut = register_waiter(tool_call_id)
    try:
        return await asyncio.wait_for(fut, timeout=timeout or APPROVAL_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        resolve(tool_call_id, "expired", "system", "审批超时，自动拒绝")
        return "expired"
    finally:
        _waiters.pop(tool_call_id, None)


def resolve(
    tool_call_id: int,
    status: str,
    decided_by: str,
    comment: str = "",
) -> Optional[Dict[str, Any]]:
    """落库 + 唤醒 Agent。status: approved | rejected | expired"""
    row = db.query_one(
        "SELECT * FROM approvals WHERE tool_call_id = ? ORDER BY id DESC LIMIT 1",
        (tool_call_id,),
    )
    if row is None or row["status"] != "pending":
        return row
    db.execute(
        "UPDATE approvals SET status = ?, decided_by = ?, comment = ?, decided_at = ? WHERE id = ?",
        (status, decided_by, comment, now_iso(), row["id"]),
    )
    fut = _waiters.get(tool_call_id)
    if fut is not None and not fut.done():
        fut.set_result(status)
    return db.query_one("SELECT * FROM approvals WHERE id = ?", (row["id"],))


def list_pending() -> list:
    rows = db.query(
        """SELECT a.*, s.code AS session_code, t.tool, t.arguments, t.risk_level, t.risk_reasons, t.reason
           FROM approvals a
           JOIN sessions s ON s.id = a.session_id
           JOIN tool_calls t ON t.id = a.tool_call_id
           WHERE a.status = 'pending'
           ORDER BY a.requested_at DESC"""
    )
    return rows

