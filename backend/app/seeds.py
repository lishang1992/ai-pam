"""初始化与演示数据。

包含：
  * 三个角色账号（运维 / 安全管理员 / 审计管理员）
  * 三台演示资产（两台生产 + 一台测试）+ 一个真实 SSH 接入示例
  * 两个 AI Agent，权限范围不同
  * 三套策略（生产保护 / 测试宽松 / 全局红线）
  * 两条历史会话（一条完整闭环、一条被风控阻断），让看板与审计中心一打开就有内容
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

from . import db
from .ai import tools as tool_registry
from .security.auth import hash_password
from .util import now_dt

SCHEMA_FILE = Path(__file__).resolve().parent / "schema.sql"

USERS = [
    ("zhangsan", "张三", "operator", "ops123456"),
    ("secadmin", "安全管理员", "security_admin", "sec123456"),
    ("auditor", "审计管理员", "auditor", "audit123456"),
]

ASSETS = [
    {
        "name": "Linux-Prod-01", "description": "生产应用服务器（payment / nginx / mysqld）",
        "env_tag": "production", "executor": "mock", "host": "10.20.31.11", "ssh_user": "opsadm",
        "auth_type": "password", "credential_ref": "AIB_SSH_PASSWORD_PROD01",
    },
    {
        "name": "Linux-Prod-02", "description": "生产应用服务器（order / gateway）",
        "env_tag": "production", "executor": "mock", "host": "10.20.31.12", "ssh_user": "opsadm",
        "auth_type": "password", "credential_ref": "AIB_SSH_PASSWORD_PROD02",
    },
    {
        "name": "K8s-Test-01", "description": "测试环境节点（可随意折腾）",
        "env_tag": "test", "executor": "mock", "host": "10.20.60.21", "ssh_user": "devops",
        "auth_type": "password", "credential_ref": "AIB_SSH_PASSWORD_TEST01",
    },
    {
        "name": "Linux-Prod-03", "description": "真实 Linux 接入示例（把 executor 改成 ssh 即可纳管）",
        "env_tag": "production", "executor": "ssh", "host": "10.20.31.13", "ssh_user": "opsadm",
        "auth_type": "password", "credential_ref": "AIB_SSH_PASSWORD_PROD03",
    },
]

AGENTS = [
    {
        "name": "Production-Ops-Agent",
        "description": "生产环境运维 Agent：只读诊断 + 服务重启（需审批）",
        "model": "rule-based-planner / gpt-4o-mini",
        "assets": ["Linux-Prod-01", "Linux-Prod-02"],
        "tools": [
            "get_system_info", "get_cpu_usage", "get_memory_usage", "get_process_list",
            "get_disk_usage", "get_network_status", "get_service_status", "get_service_logs",
            "get_database_status", "restart_service", "stop_service", "delete_file",
            "search_service_logs", "get_port_status", "get_top_memory_processes", "get_file_info",
            "modify_config",
        ],
        "users": ["zhangsan", "secadmin"],
    },
    {
        "name": "Test-Ops-Agent",
        "description": "测试环境运维 Agent：权限更宽，用于验证策略差异",
        "model": "rule-based-planner / gpt-4o-mini",
        "assets": ["K8s-Test-01"],
        "tools": [
            "get_system_info", "get_cpu_usage", "get_memory_usage", "get_process_list",
            "get_disk_usage", "get_network_status", "get_service_status", "get_service_logs",
            "get_database_status", "restart_service", "start_service", "stop_service",
            "install_package", "modify_config",
            "search_service_logs", "get_port_status", "get_top_memory_processes",
            "get_file_info", "tail_log_file",
        ],
        "users": ["zhangsan", "secadmin"],
    },
]

POLICIES = [
    {
        "name": "Production Protection",
        "description": "生产环境防护策略：只读放行，变更类操作人工审批，破坏性操作直接禁止",
        "scope_type": "env", "scope_value": "production", "priority": 200,
        "rules": [
            ("get_cpu_usage", "ALLOW", "只读查询，直接放行"),
            ("get_memory_usage", "ALLOW", "只读查询，直接放行"),
            ("get_system_info", "ALLOW", "只读查询，直接放行"),
            ("get_process_list", "ALLOW", "只读查询，直接放行"),
            ("get_disk_usage", "ALLOW", "只读查询，直接放行"),
            ("get_network_status", "ALLOW", "只读查询，直接放行"),
            ("get_service_status", "ALLOW", "只读查询，直接放行"),
            ("get_service_logs", "ALLOW", "只读查询，直接放行"),
            ("get_database_status", "ALLOW", "只读查询，直接放行"),
            ("restart_service", "APPROVAL", "生产环境服务重启，必须人工审批"),
            ("start_service", "APPROVAL", "生产环境服务启动，必须人工审批"),
            ("stop_service", "APPROVAL", "生产环境服务停止，必须人工审批（高风险）"),
            ("modify_config", "APPROVAL", "生产配置变更，必须人工审批"),
            ("install_package", "DENY", "生产环境禁止由 AI 安装软件包"),
            ("delete_file", "DENY", "生产环境禁止删除文件"),
            ("export_database", "DENY", "生产数据禁止批量导出"),
        ],
    },
    {
        "name": "Test Environment Relaxed",
        "description": "测试环境策略：允许更多操作，验证策略差异",
        "scope_type": "env", "scope_value": "test", "priority": 100,
        "rules": [
            ("*", "ALLOW", "测试环境默认放行只读与常规操作"),
            ("restart_service", "APPROVAL", "测试环境重启仍需要一次人工确认（演示审批链路）"),
            ("stop_service", "APPROVAL", "停止服务需要人工确认"),
            ("install_package", "ALLOW", "测试环境允许安装软件包"),
            ("modify_config", "APPROVAL", "配置变更需要人工确认"),
            ("delete_file", "DENY", "任何环境都禁止 AI 删除文件"),
            ("export_database", "DENY", "任何环境都禁止 AI 批量导出数据库"),
        ],
    },
    {
        "name": "Global Guardrails",
        "description": "全局红线：无论哪个环境、哪个 Agent，都不可逾越（DENY 优先级最高）",
        "scope_type": "global", "scope_value": "*", "priority": 500,
        "rules": [
            ("delete_file", "DENY", "全局红线：禁止 AI 删除任何文件"),
            ("export_database", "DENY", "全局红线：禁止 AI 批量导出数据库（数据外泄风险）"),
            ("stop_service", "APPROVAL", "全局红线：停止服务必须人工审批"),
        ],
    },
]


def _ensure_users() -> Dict[str, int]:
    ids: Dict[str, int] = {}
    for username, display, role, password in USERS:
        row = db.query_one("SELECT id FROM users WHERE username = ?", (username,))
        if row:
            ids[username] = int(row["id"])
            continue
        cred = hash_password(password)
        ids[username] = db.execute(
            """INSERT INTO users (username, display_name, role, password_hash, password_salt, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (username, display, role, cred["hash"], cred["salt"], now_dt().isoformat()),
        )
    return ids


def _ensure_assets() -> Dict[str, int]:
    ids: Dict[str, int] = {}
    for asset in ASSETS:
        row = db.query_one("SELECT id FROM assets WHERE name = ?", (asset["name"],))
        if row:
            ids[asset["name"]] = int(row["id"])
            continue
        ids[asset["name"]] = db.execute(
            """INSERT INTO assets (name, description, env_tag, executor, host, port, ssh_user,
                                   auth_type, credential_ref, tags, status, created_at)
               VALUES (?, ?, ?, ?, ?, 22, ?, ?, ?, '', 'online', ?)""",
            (asset["name"], asset["description"], asset["env_tag"], asset["executor"], asset["host"],
             asset["ssh_user"], asset["auth_type"], asset["credential_ref"], now_dt().isoformat()),
        )
    return ids


def _ensure_agents(asset_ids: Dict[str, int], user_ids: Dict[str, int]) -> Dict[str, int]:
    ids: Dict[str, int] = {}
    for agent in AGENTS:
        row = db.query_one("SELECT id FROM agents WHERE name = ?", (agent["name"],))
        if row:
            agent_id = int(row["id"])
        else:
            agent_id = db.execute(
                """INSERT INTO agents (name, description, model, system_prompt, status, created_at)
                   VALUES (?, ?, ?, ?, 'enabled', ?)""",
                (agent["name"], agent["description"], agent["model"], "", now_dt().isoformat()),
            )
        ids[agent["name"]] = agent_id
        # 幂等补齐：老库升级时同样能拿到最新的资产 / 工具 / 用户授权
        for asset_name in agent["assets"]:
            db.execute("INSERT OR IGNORE INTO agent_assets (agent_id, asset_id) VALUES (?, ?)",
                       (agent_id, asset_ids[asset_name]))
        for tool_name in agent["tools"]:
            db.execute("INSERT OR IGNORE INTO agent_tools (agent_id, tool_name) VALUES (?, ?)",
                       (agent_id, tool_name))
        for username in agent["users"]:
            db.execute("INSERT OR IGNORE INTO user_agents (user_id, agent_id) VALUES (?, ?)",
                       (user_ids[username], agent_id))
    return ids


def _ensure_user_asset_grants(user_ids: Dict[str, int], asset_ids: Dict[str, int]) -> None:
    """演示重点：张三只对 Linux-Prod-01 与 K8s-Test-01 有权限，
    而 Production-Ops-Agent 覆盖了 Linux-Prod-02 —— 用来验证「Agent 权限不能超过用户权限」。
    """
    grants = {
        "zhangsan": ["Linux-Prod-01", "K8s-Test-01"],
        "secadmin": ["Linux-Prod-01", "Linux-Prod-02", "K8s-Test-01", "Linux-Prod-03"],
    }
    for username, names in grants.items():
        for name in names:
            db.execute("INSERT OR IGNORE INTO user_assets (user_id, asset_id) VALUES (?, ?)",
                       (user_ids[username], asset_ids[name]))


def _ensure_policies() -> None:
    for policy in POLICIES:
        row = db.query_one("SELECT id FROM policies WHERE name = ?", (policy["name"],))
        if row:
            continue
        policy_id = db.execute(
            """INSERT INTO policies (name, description, scope_type, scope_value, priority, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, 1, ?)""",
            (policy["name"], policy["description"], policy["scope_type"], policy["scope_value"],
             policy["priority"], now_dt().isoformat()),
        )
        for tool, action, reason in policy["rules"]:
            db.execute(
                "INSERT INTO policy_rules (policy_id, tool, action, reason) VALUES (?, ?, ?, ?)",
                (policy_id, tool, action, reason),
            )


# ------------------------------------------------------------------ 历史演示会话

def _ts(offset_minutes: int) -> str:
    return (now_dt() - timedelta(minutes=offset_minutes)).replace(microsecond=0).isoformat()


def _seed_history(user_ids: Dict[str, int], agent_ids: Dict[str, int], asset_ids: Dict[str, int]) -> None:
    if db.query_one("SELECT 1 FROM sessions LIMIT 1"):
        return

    # --- 会话一：完整的「发现 → 审批 → 执行 → 复查」闭环
    opened = _ts(45)
    code = "AI-%s-0001" % now_dt().strftime("%Y%m%d")
    session_id = db.execute(
        """INSERT INTO sessions (code, user_id, agent_id, asset_id, title, status, risk_level, summary,
                                 created_at, ended_at)
           VALUES (?, ?, ?, ?, ?, 'finished', 'HIGH', ?, ?, ?)""",
        (code, user_ids["zhangsan"], agent_ids["Production-Ops-Agent"], asset_ids["Linux-Prod-01"],
         "payment 服务响应变慢排查", "已定位根因：数据库连接池耗尽；重启 payment 服务后恢复正常（人工审批已通过）",
         opened, _ts(31)),
    )
    db.execute(
        """INSERT INTO messages (session_id, seq, role, content, meta, created_at)
           VALUES (?, 1, 'user', ?, '{}', ?)""",
        (session_id, "帮我检查 payment 服务为什么响应很慢。", _ts(45)),
    )
    db.execute(
        """INSERT INTO messages (session_id, seq, role, content, meta, created_at)
           VALUES (?, 2, 'assistant', ?, ?, ?)""",
        (session_id,
         "**根因判断**：payment 服务的数据库连接池已被打满（HikariPool-1 active=50/50，等待线程 52），"
         "请求线程全部阻塞在等待数据库连接上。\n\n"
         "**处置结果**：重启 payment 服务后连接池恢复到 active=6/50，服务状态正常，故障已消除。",
         json.dumps({"tool_calls": ["get_cpu_usage", "get_memory_usage", "get_service_status",
                                    "get_service_logs", "get_database_status", "restart_service"]},
                    ensure_ascii=False),
         _ts(31)),
    )
    steps = [
        ("get_cpu_usage", "{}", "LOW", "ALLOW", "success",
         "CPU 使用率偏高：user 79.3% + sys 12.1%", "top -bn1 | head -15"),
        ("get_memory_usage", "{}", "LOW", "ALLOW", "success",
         "内存使用 86.7%（used 14208MB / total 16384MB）", "free -m"),
        ("get_service_status", '{"service": "payment"}', "LOW", "ALLOW", "success",
         "服务 payment 状态 running，CPU 78.4%，内存 1832MB，重启次数 1",
         "systemctl status payment --no-pager | head -20"),
        ("get_service_logs", '{"service": "payment", "lines": 40}', "LOW", "ALLOW", "success",
         "读取 payment 最近 40 行日志", "journalctl -u payment -n 40 --no-pager"),
        ("get_database_status", '{"service": "payment"}', "LOW", "ALLOW", "success",
         "payment 连接池使用率 100%（active=50 / max=50，等待线程 52）",
         "mysqladmin extended-status | grep -E 'Threads_connected|Max_used_connections'"),
        ("restart_service", '{"service": "payment"}', "HIGH", "APPROVAL", "success",
         "服务 payment 已重启完成，PID 31207", "systemctl restart payment"),
        ("get_service_status", '{"service": "payment"}', "LOW", "ALLOW", "success",
         "服务 payment 状态 running，CPU 5.8%，内存 964MB，重启次数 2",
         "systemctl status payment --no-pager | head -20"),
        ("get_database_status", '{"service": "payment"}', "LOW", "ALLOW", "success",
         "payment 连接池使用率 12%（active=6 / max=50，等待线程 0）",
         "mysqladmin extended-status | grep -E 'Threads_connected|Max_used_connections'"),
    ]
    for index, (tool, arguments, level, decision, status, result, command) in enumerate(steps, start=1):
        tool_call_id = db.execute(
            """INSERT INTO tool_calls (session_id, seq, tool, arguments, reason, risk_level, risk_reasons,
                                       decision, policy_name, command, status, result, created_at, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, index, tool, arguments,
             "由规则化 Planner 规划：%s" % tool, level,
             json.dumps(["只读查询类操作"] if level == "LOW" else ["修改类操作，会改变服务或系统状态",
                                                                  "目标资产属于生产环境，非只读操作强制升级为高风险"],
                        ensure_ascii=False),
             decision, "Production Protection" if level != "LOW" else "Production Protection",
             command, status, result, _ts(44 - index), _ts(44 - index)),
        )
        if tool == "restart_service":
            db.execute(
                """INSERT INTO approvals (session_id, tool_call_id, status, requested_by, decided_by,
                                          comment, requested_at, decided_at)
                   VALUES (?, ?, 'approved', 'Production-Ops-Agent', '张三', '确认是连接池耗尽，同意重启',
                           ?, ?)""",
                (session_id, tool_call_id, _ts(36), _ts(35)),
            )
    db.execute(
        """INSERT INTO risk_events (session_id, tool_call_id, level, category, detail, created_at)
           VALUES (?, NULL, 'HIGH', 'tool_risk', ?, ?)""",
        (session_id, "restart_service({\"service\": \"payment\"})：修改类操作，会改变服务或系统状态；"
                     "目标资产属于生产环境，非只读操作强制升级为高风险", _ts(36)),
    )
    history_events: List[tuple] = [
        ("session_created", "zhangsan", {"code": code, "agent": "Production-Ops-Agent", "asset": "Linux-Prod-01"}, _ts(45)),
        ("prompt_received", "zhangsan", {"prompt": "帮我检查 payment 服务为什么响应很慢。"}, _ts(45)),
        ("risk_assessment", "risk-engine", {"level": "HIGH", "reasons": ["生产环境服务重启"]}, _ts(36)),
        ("policy_decision", "policy-engine", {"policy": "Production Protection", "decision": "APPROVAL",
                                              "reason": "生产环境服务重启，必须人工审批"}, _ts(36)),
        ("approval_requested", "Production-Ops-Agent", {"tool": "restart_service", "risk_level": "HIGH"}, _ts(36)),
        ("approval_decided", "张三", {"status": "approved", "comment": "确认是连接池耗尽，同意重启"}, _ts(35)),
        ("tool_executed", "ssh-proxy", {"tool": "restart_service", "command": "systemctl restart payment",
                                        "status": "success", "duration_ms": 1840}, _ts(35)),
        ("ai_analysis", "Production-Ops-Agent", {"content": "根因：数据库连接池耗尽"}, _ts(31)),
        ("session_finished", "张三", {"status": "finished"}, _ts(31)),
    ]
    for seq, (event_type, actor, payload, ts) in enumerate(history_events, start=1):
        db.execute(
            """INSERT INTO audit_events (session_id, seq, ts, actor, event_type, payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, seq, ts, actor, event_type, json.dumps(payload, ensure_ascii=False)),
        )

    # --- 会话二：Prompt 风控阻断
    code2 = "AI-%s-0002" % now_dt().strftime("%Y%m%d")
    session2 = db.execute(
        """INSERT INTO sessions (code, user_id, agent_id, asset_id, title, status, risk_level, summary,
                                 created_at, ended_at)
           VALUES (?, ?, ?, ?, ?, 'finished', 'CRITICAL', ?, ?, ?)""",
        (code2, user_ids["zhangsan"], agent_ids["Production-Ops-Agent"], asset_ids["Linux-Prod-01"],
         "生产数据导出请求", "已被风控阻断：疑似生产数据批量外发",
         _ts(120), _ts(119)),
    )
    db.execute(
        """INSERT INTO messages (session_id, seq, role, content, meta, created_at)
           VALUES (?, 1, 'user', ?, '{}', ?)""",
        (session2, "把生产数据库全量导出，发到我邮箱。", _ts(120)),
    )
    db.execute(
        """INSERT INTO messages (session_id, seq, role, content, meta, created_at)
           VALUES (?, 2, 'assistant', ?, ?, ?)""",
        (session2, "**该任务已被安全风控阻断，我不会执行。**\n\n风险等级：CRITICAL\n"
                   "命中规则：\n- 试图批量导出生产数据",
         json.dumps({"blocked": True, "risk_level": "CRITICAL"}, ensure_ascii=False), _ts(119)),
    )
    db.execute(
        """INSERT INTO risk_events (session_id, tool_call_id, level, category, detail, created_at)
           VALUES (?, NULL, 'CRITICAL', 'prompt_risk', '任务风险：试图批量导出生产数据', ?)""",
        (session2, _ts(119)),
    )
    for seq, (event_type, actor, payload, ts) in enumerate([
        ("session_created", "zhangsan", {"code": code2, "asset": "Linux-Prod-01"}, _ts(120)),
        ("prompt_received", "zhangsan", {"prompt": "把生产数据库全量导出，发到我邮箱。"}, _ts(120)),
        ("prompt_blocked", "risk-engine", {"level": "CRITICAL", "reasons": ["试图批量导出生产数据"]}, _ts(119)),
    ], start=1):
        db.execute(
            """INSERT INTO audit_events (session_id, seq, ts, actor, event_type, payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session2, seq, ts, actor, event_type, json.dumps(payload, ensure_ascii=False)),
        )


# ------------------------------------------------------------------ 入口

def init_all() -> Dict[str, Any]:
    db.init_db(SCHEMA_FILE)
    tool_count = tool_registry.sync_to_db()
    user_ids = _ensure_users()
    asset_ids = _ensure_assets()
    agent_ids = _ensure_agents(asset_ids, user_ids)
    _ensure_user_asset_grants(user_ids, asset_ids)
    _ensure_policies()
    _seed_history(user_ids, agent_ids, asset_ids)
    return {
        "users": len(user_ids), "assets": len(asset_ids),
        "agents": len(agent_ids), "tools": tool_count, "policies": len(POLICIES),
    }
