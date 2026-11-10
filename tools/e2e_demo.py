"""端到端演示脚本：不打开浏览器，直接把 Demo 的完整闭环跑一遍并打印审计链路。

用法（服务需已启动，或在脚本里自动启动）：
    python tools/e2e_demo.py                 # 自动启动/复用 http://127.0.0.1:8000
    python tools/e2e_demo.py --no-start      # 复用已经跑起来的服务

验证内容（与 PRD 第二十节验收标准一一对应）：
    RDP/浏览器登录 → 创建 AI 会话 → 自然语言任务 → AI 调用 Tool → Tool 访问 Linux
    → AI 读取结果 → 提出高风险操作 → 策略拦截并请求人工审批 → 审批通过执行
    → 审计中心还原全过程
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.getenv("AIB_BASE", "http://127.0.0.1:8000")


def http(method: str, path: str, body=None, token: str = "") -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def wait_for_server(timeout: int = 40) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http("GET", "/api/health")
            return
        except Exception:
            time.sleep(0.6)
    raise SystemExit("服务未能在 %d 秒内启动，请检查 uvicorn 日志" % timeout)


def watch_session(session_id: int, token: str, approval_handler) -> dict:
    """轮询会话状态，模拟前端通过 WebSocket 看到的实时过程。"""
    last_steps = 0
    seen_approvals = set()
    deadline = time.time() + 90
    while time.time() < deadline:
        session = http("GET", "/api/sessions/%d" % session_id, token=token)
        steps = session.get("tool_calls", [])
        for step in steps[last_steps:]:
            print("   ├─ %-22s 风险=%-8s 判定=%-8s 状态=%s" % (
                step["tool"], step["risk_level"], step["decision"], step["status"]))
            if step.get("result"):
                print("   │    结果：%s" % str(step["result"]).splitlines()[0][:110])
            if step.get("error"):
                print("   │    拦截：%s" % step["error"][:110])
        last_steps = len(steps)

        for approval in session.get("approvals", []):
            if approval["status"] == "pending" and approval["id"] not in seen_approvals:
                seen_approvals.add(approval["id"])
                approval_handler(session_id, approval)

        if not session.get("running") and steps and session["status"] not in ("waiting_approval",):
            messages = session.get("messages", [])
            if messages and messages[-1]["role"] == "assistant":
                return session
        time.sleep(0.5)
    raise SystemExit("等待会话执行完成超时")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-start", action="store_true", help="复用已启动的服务")
    parser.add_argument("--prompt", default="帮我检查 payment 服务为什么响应很慢。")
    args = parser.parse_args()

    process = None
    if not args.no_start:
        try:
            http("GET", "/api/health")
        except Exception:
            print("启动后端服务…")
            env = dict(os.environ, AIB_DEMO_MODE="1")
            python = str(ROOT / ".venv" / "bin" / "python")
            if not Path(python).exists():
                python = sys.executable
            process = subprocess.Popen([python, str(ROOT / "run.py"), "--host", "127.0.0.1"],
                                       env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            wait_for_server()

    try:
        print("\n[1] 登录 AI 安全堡垒机（浏览器 / RDP 中的同一入口）")
        login = http("POST", "/api/auth/login", {"username": "zhangsan", "password": "ops123456"})
        token = login["token"]
        print("    ✔ 登录成功：%s（%s）" % (login["user"]["display_name"], login["user"]["role"]))

        http("POST", "/api/demo/reset", {}, token)
        agents = http("GET", "/api/agents", token=token)
        assets = http("GET", "/api/assets", token=token)
        agent = next(a for a in agents if a["name"] == "Production-Ops-Agent")
        asset = next(a for a in assets if a["name"] == "Linux-Prod-01")
        print("    ✔ 选择 Agent=%s，目标资产=%s（%s）" % (agent["name"], asset["name"], asset["env_tag"]))

        print("\n[2] 创建 AI 运维会话")
        session = http("POST", "/api/sessions",
                       {"agent_id": agent["id"], "asset_id": asset["id"], "title": "E2E 演示：payment 响应变慢"},
                       token)
        print("    ✔ 会话编号：%s" % session["code"])

        print("\n[3] 下达自然语言任务：%s" % args.prompt)
        http("POST", "/api/sessions/%d/prompt" % session["id"], {"prompt": args.prompt}, token)

        def approve(session_id: int, approval: dict) -> None:
            print("\n[6] 安全控制介入：高风险操作请求人工审批")
            detail = http("GET", "/api/sessions/%d" % session_id, token=token)
            call = next(c for c in detail["tool_calls"] if c["id"] == approval["tool_call_id"])
            print("    ⚠ 工具=%s 风险=%s 判定=%s" % (call["tool"], call["risk_level"], call["decision"]))
            for reason in call["risk_reasons"]:
                print("       · %s" % reason)
            print("    命令：%s" % call["command"])
            print("\n[7] 审批人点击「允许执行」")
            http("POST", "/api/approvals/%d/decision" % approval["tool_call_id"],
                 {"decision": "approved", "comment": "E2E 演示自动审批"}, token)
            print("    ✔ 审批通过")

        print("\n[4/5/8] AI 自动分析并在受控通道中执行")
        final = watch_session(session["id"], token, approve)

        print("\n[9] 审计中心还原完整链路")
        bundle = http("GET", "/api/audit/sessions/%d" % session["id"], token=token)
        for event in bundle["events"]:
            print("    %s  %-12s %s" % (event["ts"][11:19], event["label"],
                                        str((event["payload"] or {}).get("tool") or
                                            (event["payload"] or {}).get("status") or "")[:40]))
        calls = bundle["tool_calls"]
        blocked = [c for c in calls if c["status"] in ("blocked", "denied")]

        # 原则五：会话结束后临时权限立即失效
        http("POST", "/api/sessions/%d/finish" % session["id"], {}, token)
        closed = http("GET", "/api/audit/sessions/%d" % session["id"], token=token)
        print("    %s  %s" % (closed["events"][-1]["ts"][11:19], closed["events"][-1]["label"]))

        print("\n" + "=" * 70)
        print("现场结论：")
        print("  · 工具调用 %d 次，其中被安全策略拦截 %d 次" % (len(calls), len(blocked)))
        print("  · 人工审批记录 %d 条" % len(closed["approvals"]))
        print("  · 审计事件 %d 条（Prompt / AI / Tool / 风险 / 审批 / 命令 / 结果 全留痕）" % len(closed["events"]))
        print("  · 会话风险等级：%s，最终状态：%s（临时权限已失效）"
              % (closed["session"]["risk_level"], closed["session"]["status"]))
        print("-" * 70)
        print(final["messages"][-1]["content"][:1200])
        print("=" * 70)
        return 0
    finally:
        if process is not None:
            process.terminate()


if __name__ == "__main__":
    sys.exit(main())
