"""WebSocket：把 Agent 的每一步实时推给前端。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import db
from ..events import bus

router = APIRouter(tags=["实时"])


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    """全局事件流（看板、审批中心的实时刷新）。"""
    await websocket.accept()
    queue = bus.subscribe()
    try:
        while True:
            message = await queue.get()
            if message["channel"] == "sessions":
                await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(queue)


@router.websocket("/ws/sessions/{session_id}")
async def ws_session(websocket: WebSocket, session_id: int) -> None:
    """单会话事件流：AI 控制台与实时会话页面使用。"""
    await websocket.accept()
    queue = bus.subscribe()
    try:
        snapshot = _snapshot(session_id)
        await websocket.send_json({"channel": "snapshot", "payload": snapshot})
        while True:
            message = await queue.get()
            if message["channel"] != "session":
                continue
            if int(message["payload"].get("session_id", -1)) != int(session_id):
                continue
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        bus.unsubscribe(queue)


def _snapshot(session_id: int) -> Dict[str, Any]:
    from ..util import loads

    session = db.query_one(
        """SELECT s.*, u.display_name AS user_name, ag.name AS agent_name, ast.name AS asset_name,
                  ast.env_tag, ast.executor
           FROM sessions s JOIN users u ON u.id = s.user_id
           JOIN agents ag ON ag.id = s.agent_id JOIN assets ast ON ast.id = s.asset_id
           WHERE s.id = ?""",
        (session_id,),
    )
    if session is None:
        return {}
    messages = db.query("SELECT * FROM messages WHERE session_id = ? ORDER BY seq", (session_id,))
    for message in messages:
        message["meta"] = loads(message.get("meta"), {}) or {}
    tool_calls = db.query("SELECT * FROM tool_calls WHERE session_id = ? ORDER BY seq", (session_id,))
    for call in tool_calls:
        call["arguments"] = loads(call.get("arguments"), {}) or {}
        call["risk_reasons"] = loads(call.get("risk_reasons"), []) or []
    approvals = db.query(
        "SELECT * FROM approvals WHERE session_id = ? AND status = 'pending' ORDER BY id", (session_id,)
    )
    return {"session": session, "messages": messages, "tool_calls": tool_calls, "approvals": approvals}
