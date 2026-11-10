"""Agent Runner：把「用户任务」变成「受控的工具执行链」。

一次 run_turn 的完整链路：
    Prompt 安全检测 → 规划下一步 → Tool Gateway 授权 → 策略 → 风险
    → （高风险）人工审批 → 执行代理 → 结果回灌 → 生成分析结论 → 全程审计
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from .. import db
from ..audit import logger as audit
from ..events import bus
from ..security import approvals, gateway, risk
from ..settings import APPROVAL_TIMEOUT_SECONDS, MAX_TOOL_STEPS, RISK_BLOCK_AT
from ..util import dumps, now_iso, session_code
from . import llm, planner, prompts, tools as tool_registry

# 正在执行的会话任务，便于终止会话 / 测试等待
ACTIVE_RUNS: Dict[int, asyncio.Task] = {}


class SessionBusy(RuntimeError):
    pass


# ------------------------------------------------------------------ 基础读取

def _load(session_id: int) -> Dict[str, Any]:
    session = db.query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
    if session is None:
        raise ValueError("会话不存在")
    user = db.query_one("SELECT * FROM users WHERE id = ?", (session["user_id"],))
    agent = db.query_one("SELECT * FROM agents WHERE id = ?", (session["agent_id"],))
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (session["asset_id"],))
    return {"session": session, "user": user, "agent": agent, "asset": asset}


def _next_seq(session_id: int, table: str) -> int:
    row = db.query_one(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM %s WHERE session_id = ?" % table, (session_id,)
    )
    return int((row or {}).get("seq") or 0) + 1


def _add_message(session_id: int, role: str, content: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    seq = _next_seq(session_id, "messages")
    created = now_iso()
    msg_id = db.execute(
        """INSERT INTO messages (session_id, seq, role, content, meta, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (session_id, seq, role, content, dumps(meta or {}), created),
    )
    return {"id": msg_id, "session_id": session_id, "seq": seq, "role": role,
            "content": content, "meta": meta or {}, "created_at": created}


def _bump_risk(session_id: int, level: str) -> None:
    row = db.query_one("SELECT risk_level FROM sessions WHERE id = ?", (session_id,))
    current = (row or {}).get("risk_level") or "LOW"
    if risk.rank(level) > risk.rank(current):
        db.execute("UPDATE sessions SET risk_level = ? WHERE id = ?", (level, session_id))


def _set_status(session_id: int, status: str, summary: Optional[str] = None) -> None:
    if summary is None:
        db.execute("UPDATE sessions SET status = ? WHERE id = ?", (status, session_id))
    else:
        db.execute("UPDATE sessions SET status = ?, summary = ? WHERE id = ?", (status, summary, session_id))


# ------------------------------------------------------------------ 会话生命周期

def create_session(user: Dict[str, Any], agent_id: int, asset_id: int, title: str) -> Dict[str, Any]:
    agent = db.query_one("SELECT * FROM agents WHERE id = ?", (agent_id,))
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if agent is None or asset is None:
        raise ValueError("Agent 或资产不存在")
    if agent["status"] != "enabled":
        raise ValueError("该 Agent 已被停用，禁止创建会话")
    # 第一道防线：用户本人必须被授权使用该 Agent（Agent 权限不能超过用户权限）
    granted = db.query_one(
        "SELECT 1 FROM user_agents WHERE user_id = ? AND agent_id = ?", (user["id"], agent_id)
    )
    if granted is None:
        raise ValueError("您没有被授权使用 Agent「%s」，请联系安全管理员" % agent["name"])

    today = now_iso()[:10].replace("-", "")
    row = db.query_one("SELECT COUNT(*) AS n FROM sessions")
    seq = int((row or {}).get("n") or 0) + 1
    code = session_code(seq)
    session_id = db.execute(
        """INSERT INTO sessions (code, user_id, agent_id, asset_id, title, status, risk_level, created_at)
           VALUES (?, ?, ?, ?, ?, 'active', 'LOW', ?)""",
        (code, user["id"], agent_id, asset_id, title or "AI 运维会话", now_iso()),
    )
    audit.record("session_created", session_id=session_id, actor=user["username"], payload={
        "code": code, "agent": agent["name"], "asset": asset["name"], "title": title,
        "env_tag": asset["env_tag"], "user": user["display_name"],
    })
    result = {
        "id": session_id, "code": code, "title": title or "AI 运维会话",
        "user_id": user["id"], "agent_id": agent_id, "asset_id": asset_id,
        "agent_name": agent["name"], "asset_name": asset["name"],
        "status": "active", "risk_level": "LOW", "created_at": now_iso(),
    }
    bus.publish_session(session_id, {"type": "session_created", "session": result})
    bus.publish("sessions", {"type": "session_created", "session": result})
    return result


def finish_session(session_id: int, actor: str, terminated: bool = False) -> None:
    status = "terminated" if terminated else "finished"
    _set_status(session_id, status)
    db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now_iso(), session_id))
    # 原则五：会话结束后临时权限立即失效（清理待审批请求）
    pending = db.query("SELECT tool_call_id FROM approvals WHERE session_id = ? AND status = 'pending'", (session_id,))
    for row in pending:
        approvals.resolve(row["tool_call_id"], "expired", actor, "会话已结束，审批自动失效")
    audit.record(
        "session_terminated" if terminated else "session_finished",
        session_id=session_id, actor=actor, payload={"status": status},
    )
    bus.publish_session(session_id, {"type": "session_finished", "status": status})


# ------------------------------------------------------------------ 单步执行

async def _execute_step(
    ctx: Dict[str, Any],
    step: Dict[str, Any],
) -> Dict[str, Any]:
    session, user, agent, asset = ctx["session"], ctx["user"], ctx["agent"], ctx["asset"]
    session_id = int(session["id"])
    tool = tool_registry.get_tool(step["tool"])
    seq = _next_seq(session_id, "tool_calls")
    record: Dict[str, Any] = {
        "seq": seq, "tool": step["tool"], "arguments": step.get("arguments") or {},
        "reason": step.get("reason", ""), "status": "pending", "summary": "", "stdout": "",
        "data": {}, "error": "", "command": "", "risk_level": "LOW", "risk_reasons": [],
        "decision": "DENY", "policy_name": "", "policy_reason": "", "tool_call_id": None,
        "approval_status": "",
    }

    if not tool:
        record["status"] = "blocked"
        record["error"] = "工具不存在或未在白名单内"
        record["policy_name"] = "Tool Registry"
        record["policy_reason"] = "AI 只能调用已注册的工具"
        audit.record("tool_denied", session_id=session_id, actor="tool-gateway",
                     payload={"tool": step["tool"], "reason": record["error"]})
        return record

    try:
        arguments = tool_registry.validate_arguments(tool, step.get("arguments") or {})
    except tool_registry.ToolArgumentError as exc:
        record.update({
            "status": "blocked", "error": str(exc), "policy_name": "参数白名单校验",
            "policy_reason": "工具参数未通过白名单校验，拒绝下发到目标主机",
        })
        audit.record("tool_denied", session_id=session_id, actor="tool-gateway",
                     payload={"tool": tool["name"], "arguments": step.get("arguments"), "reason": str(exc)})
        bus.publish_session(session_id, {"type": "tool_denied", "step": record})
        return record
    record["arguments"] = arguments

    auth = gateway.authorize(user, agent, asset, tool, arguments)
    command = tool_registry.build_command(tool, arguments)
    record.update({
        "risk_level": auth["risk_level"], "risk_reasons": auth["risk_reasons"],
        "decision": auth["decision"], "policy_name": auth["policy_name"],
        "policy_reason": auth["policy_reason"], "command": command,
        "decision_source": auth.get("source", "policy"),
        "checks": auth["checks"],
    })

    created_at = now_iso()
    tool_call_id = db.execute(
        """INSERT INTO tool_calls
           (session_id, seq, tool, arguments, reason, risk_level, risk_reasons, decision,
            policy_name, command, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        (session_id, seq, tool["name"], dumps(arguments), record["reason"], auth["risk_level"],
         dumps(auth["risk_reasons"]), auth["decision"], auth["policy_name"], command, created_at),
    )
    record["tool_call_id"] = tool_call_id
    record["id"] = tool_call_id
    record["created_at"] = created_at

    audit.record("ai_tool_request", session_id=session_id, actor=agent["name"], payload={
        "tool": tool["name"], "arguments": arguments, "reason": record["reason"],
        "command": command, "tool_call_id": tool_call_id, "seq": seq,
    })
    _bump_risk(session_id, auth["risk_level"])
    if risk.rank(auth["risk_level"]) >= risk.rank("MEDIUM"):
        audit.risk_event(auth["risk_level"], "tool_risk",
                         "%s(%s)：%s" % (tool["name"], json_brief(arguments), "；".join(auth["risk_reasons"])),
                         session_id=session_id, tool_call_id=tool_call_id)
    audit.record("risk_assessment", session_id=session_id, actor="risk-engine", payload={
        "level": auth["risk_level"], "reasons": auth["risk_reasons"], "tool_call_id": tool_call_id,
    })
    audit.record("policy_decision", session_id=session_id, actor="policy-engine", payload={
        "policy": auth["policy_name"], "decision": auth["decision"],
        "reason": auth["policy_reason"], "checks": auth["checks"], "tool_call_id": tool_call_id,
    })
    bus.publish_session(session_id, {"type": "tool_call", "step": record})

    # ---------------- 拒绝
    if auth["decision"] == "DENY":
        # 权限不足 / 风险阻断 -> blocked（不允许任何审批绕过）；命中 DENY 策略 -> denied
        status = "denied" if auth.get("source") == "policy" else "blocked"
        db.execute("UPDATE tool_calls SET status = ?, error = ?, finished_at = ? WHERE id = ?",
                   (status, auth["policy_reason"], now_iso(), tool_call_id))
        record.update({"status": status, "error": auth["policy_reason"], "summary": "已被安全策略拦截"})
        audit.record("tool_denied", session_id=session_id, actor="policy-engine", payload={
            "tool": tool["name"], "decision": auth["decision"], "reason": auth["policy_reason"],
            "policy": auth["policy_name"], "tool_call_id": tool_call_id,
        })
        bus.publish_session(session_id, {"type": "tool_denied", "step": record})
        return record

    # ---------------- 人工审批
    if auth["decision"] == "APPROVAL":
        _set_status(session_id, "waiting_approval")
        approval = approvals.create_request(session_id, tool_call_id, requested_by=agent["name"])
        approvals.register_waiter(tool_call_id)
        audit.record("approval_requested", session_id=session_id, actor=agent["name"], payload={
            "tool": tool["name"], "arguments": arguments, "command": command,
            "risk_level": auth["risk_level"], "risk_reasons": auth["risk_reasons"],
            "policy": auth["policy_name"], "approval_id": approval.get("id"),
            "expires_at": approval.get("expires_at"), "tool_call_id": tool_call_id,
        })
        bus.publish_session(session_id, {
            "type": "approval_required", "step": record,
            "approval": {
                "id": approval.get("id"), "tool_call_id": tool_call_id,
                "tool": tool["name"], "arguments": arguments, "command": command,
                "risk_level": auth["risk_level"], "risk_reasons": auth["risk_reasons"],
                "policy_name": auth["policy_name"], "policy_reason": auth["policy_reason"],
                "expires_at": approval.get("expires_at"), "status": "pending",
            },
        })
        decision = await approvals.wait_for_decision(tool_call_id, timeout=APPROVAL_TIMEOUT_SECONDS)
        _set_status(session_id, "active")
        record["approval_status"] = decision
        approval_row = db.query_one("SELECT * FROM approvals WHERE id = ?", (approval.get("id"),))
        audit.record("approval_decided", session_id=session_id, actor=(approval_row or {}).get("decided_by") or "-",
                     payload={
                         "approval_id": approval.get("id"),
                         "status": decision,
                         "comment": (approval_row or {}).get("comment", ""),
                         "decided_by": (approval_row or {}).get("decided_by", ""),
                         "tool": tool["name"], "tool_call_id": tool_call_id,
                     })
        bus.publish_session(session_id, {"type": "approval_decided", "step": record,
                                         "approval": dict(approval_row or {})})
        if decision != "approved":
            reason = {"rejected": "人工审批被拒绝", "expired": "审批超时自动拒绝"}.get(decision, "审批未通过")
            db.execute("UPDATE tool_calls SET status = 'denied', error = ?, finished_at = ? WHERE id = ?",
                       (reason, now_iso(), tool_call_id))
            record.update({"status": "denied", "error": reason, "summary": reason})
            audit.record("tool_denied", session_id=session_id, actor="approval-center",
                         payload={"tool": tool["name"], "reason": reason, "tool_call_id": tool_call_id})
            bus.publish_session(session_id, {"type": "tool_denied", "step": record})
            return record

    # ---------------- 执行
    db.execute("UPDATE tool_calls SET status = 'running' WHERE id = ?", (tool_call_id,))
    record["status"] = "running"
    bus.publish_session(session_id, {"type": "tool_running", "step": record})
    started = time.time()
    try:
        result = gateway.execute(asset, tool, arguments, command)
        duration = int((time.time() - started) * 1000)
        ok = int(result.get("exit_code", 0)) == 0 and not result.get("error")
        status = "success" if ok else "failed"
        summary = result.get("summary") or ""
        stdout = result.get("stdout") or ""
        data = result.get("data") or {}
        db.execute(
            """UPDATE tool_calls SET status = ?, result = ?, error = ?, command = ?, finished_at = ?, duration_ms = ?
               WHERE id = ?""",
            (status, (stdout or summary)[:20000], result.get("error", ""), result.get("command", command),
             now_iso(), duration, tool_call_id),
        )
        record.update({"status": status, "summary": summary, "stdout": stdout, "data": data,
                       "error": result.get("error", ""), "duration_ms": duration,
                       "executor": result.get("executor")})
        audit.record("tool_executed", session_id=session_id, actor="ssh-proxy", payload={
            "tool": tool["name"], "command": result.get("command", command), "executor": result.get("executor"),
            "target": asset["name"], "status": status, "summary": summary,
            "exit_code": result.get("exit_code"), "duration_ms": duration,
            "risk_level": auth["risk_level"], "decision": auth["decision"],
            "approval": record.get("approval_status") or "not_required",
            "stdout_excerpt": stdout[:1500], "tool_call_id": tool_call_id,
        })
        if not ok:
            audit.risk_event("MEDIUM", "execution_failure",
                             "工具 %s 执行失败：%s" % (tool["name"], result.get("error", "")[:200]),
                             session_id=session_id, tool_call_id=tool_call_id)
        bus.publish_session(session_id, {"type": "tool_result", "step": record})
    except Exception as exc:  # noqa: BLE001 —— 执行异常必须落审计，不能静默
        duration = int((time.time() - started) * 1000)
        db.execute(
            "UPDATE tool_calls SET status = 'failed', error = ?, finished_at = ?, duration_ms = ? WHERE id = ?",
            (str(exc), now_iso(), duration, tool_call_id),
        )
        record.update({"status": "failed", "error": str(exc), "summary": "执行异常", "duration_ms": duration})
        audit.record("tool_failed", session_id=session_id, actor="ssh-proxy",
                     payload={"tool": tool["name"], "error": str(exc), "tool_call_id": tool_call_id})
        bus.publish_session(session_id, {"type": "tool_result", "step": record})
    return record


def json_brief(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)[:200]


# ------------------------------------------------------------------ 主循环

async def run_turn(session_id: int, prompt: str) -> Dict[str, Any]:
    ctx = _load(session_id)
    session, user, agent, asset = ctx["session"], ctx["user"], ctx["agent"], ctx["asset"]
    if session["status"] in ("finished", "terminated"):
        raise ValueError("会话已结束，无法继续对话")

    ctx["service"] = planner.extract_service(prompt) or "nginx"
    ctx["service_explicit"] = planner.extract_service(prompt) is not None
    ctx["intent"] = planner.detect_intent(prompt)
    ctx["results"] = []

    user_message = _add_message(session_id, "user", prompt)
    audit.record("prompt_received", session_id=session_id, actor=user["username"],
                 payload={"prompt": prompt, "intent": ctx["intent"], "service": ctx["service"]})
    bus.publish_session(session_id, {"type": "user_message", "message": user_message})

    # 第一步：Prompt 安全检测（在 AI 规划之前）
    prompt_level, prompt_reasons = risk.scan_prompt(prompt)
    if risk.rank(prompt_level) >= risk.rank("MEDIUM"):
        audit.risk_event(prompt_level, "prompt_risk", "任务风险：%s" % "；".join(prompt_reasons),
                         session_id=session_id)
        _bump_risk(session_id, prompt_level)
    if risk.rank(prompt_level) >= risk.rank(RISK_BLOCK_AT):
        content = (
            "**该任务已被安全风控阻断，我不会执行。**\n\n"
            "风险等级：%s\n命中规则：\n%s\n\n"
            "原因：%s\n\n"
            "如果你确实有业务需要，请通过正式的数据导出流程或让管理员在堡垒机中调整策略，"
            "本次请求已完整记录到审计中心。"
            % (prompt_level, "\n".join("- " + r for r in prompt_reasons),
               "该请求可能导致敏感数据外泄或绕过安全控制")
        )
        audit.record("prompt_blocked", session_id=session_id, actor="risk-engine",
                     payload={"level": prompt_level, "reasons": prompt_reasons, "prompt": prompt})
        message = _add_message(session_id, "assistant", content, {"blocked": True, "risk_level": prompt_level})
        bus.publish_session(session_id, {"type": "assistant_message", "message": message,
                                         "blocked": True, "risk_level": prompt_level})
        _set_status(session_id, "active")
        return {"status": "blocked", "risk_level": prompt_level, "message": message}

    # 第二步：规划 + 执行循环
    tool_schemas, tool_names = tool_registry.agent_tool_schemas(int(agent["id"]))
    llm_messages: List[Dict[str, Any]] = []
    if llm.available():
        llm_messages = [
            {"role": "system", "content": prompts.build_system_prompt(asset, tool_names)},
            {"role": "user", "content": prompt},
        ]

    final_content: Optional[str] = None
    steps = 0
    while steps < MAX_TOOL_STEPS:
        steps += 1
        if llm.available():
            try:
                message = await llm.chat(llm_messages, tool_schemas)
                parsed = llm.parse_tool_call(message)
                if parsed is None:
                    final_content = message.get("content") or ""
                    break
                action = parsed
            except Exception as exc:  # 大模型故障时降级到本地 Planner，保证服务不中断
                audit.record("ai_analysis", session_id=session_id, actor="llm-gateway",
                             payload={"error": str(exc), "fallback": "rule-based-planner"})
                action = planner.plan_next(prompt, ctx)
        else:
            action = planner.plan_next(prompt, ctx)

        if action.get("kind") == "final":
            final_content = action.get("content") or ""
            break

        bus.publish_session(session_id, {
            "type": "ai_plan", "tool": action.get("tool"), "arguments": action.get("arguments"),
            "reason": action.get("reason"),
        })
        record = await _execute_step(ctx, action)
        ctx["results"].append(record)

        if llm.available():
            llm_messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": action.get("call_id") or "call_%d" % steps,
                    "type": "function",
                    "function": {"name": action["tool"], "arguments": json_brief(action.get("arguments") or {})},
                }],
            })
            llm_messages.append({
                "role": "tool",
                "tool_call_id": action.get("call_id") or "call_%d" % steps,
                "content": (record.get("stdout") or record.get("summary") or record.get("error"))[:4000],
            })

    if final_content is None:
        final_content = planner.compose_final(prompt, ctx)

    if not llm.available():
        final_content += "\n\n> " + prompts.RULE_PLAN_NOTICE

    message = _add_message(session_id, "assistant", final_content, {
        "tool_calls": [r["tool"] for r in ctx["results"]],
        "risk_level": max([r.get("risk_level", "LOW") for r in ctx["results"]] + ["LOW"], key=risk.rank),
    })
    audit.record("ai_analysis", session_id=session_id, actor=agent["name"], payload={
        "content": final_content, "steps": steps,
        "tools": [r["tool"] for r in ctx["results"]],
        "risk_level": max([r.get("risk_level", "LOW") for r in ctx["results"]] + ["LOW"], key=risk.rank),
    })
    bus.publish_session(session_id, {"type": "assistant_message", "message": message})
    _set_status(session_id, "active", summary=final_content[:200])
    return {"status": "ok", "message": message, "results": ctx["results"]}


def spawn_turn(session_id: int, prompt: str) -> asyncio.Task:
    """后台执行一轮对话，Web 端通过 WebSocket 实时接收每一步。"""
    existing = ACTIVE_RUNS.get(session_id)
    if existing is not None and not existing.done():
        raise SessionBusy("该会话正在执行上一条任务，请等待其完成")

    async def _wrapper() -> Any:
        try:
            return await run_turn(session_id, prompt)
        except Exception as exc:  # noqa: BLE001
            audit.record("tool_failed", session_id=session_id, actor="agent-runner",
                         payload={"error": "会话执行异常：%s" % exc})
            bus.publish_session(session_id, {"type": "error", "message": str(exc)})
            _set_status(session_id, "active")
            raise
        finally:
            ACTIVE_RUNS.pop(session_id, None)

    task = asyncio.ensure_future(_wrapper())
    ACTIVE_RUNS[session_id] = task
    return task
