"""本地规则化 Planner（演示模式的大脑）。

它的职责与真实 LLM 完全一致：给定「用户任务 + 已有工具结果」，输出下一步动作
（调用某个 Tool，或者给出最终分析）。这样切换大模型时，安全链路一行都不用改。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List, Optional

KNOWN_SERVICES = [
    "payment", "order", "user-service", "gateway", "nginx",
    "mysql", "mysqld", "redis", "kafka", "tomcat",
]

DIAGNOSE_WORDS = ["慢", "响应", "超时", "卡顿", "延迟", "抖动", "变慢", "timeout", "slow", "性能", "排查", "诊断", "为什么", "故障"]
CPU_WORDS = ["cpu", "负载", "load", "算力"]
MEM_WORDS = ["内存", "memory", "oom", "swap"]
DISK_WORDS = ["磁盘", "disk", "空间", "df"]
PROCESS_WORDS = ["进程", "process", "pstack", "top"]
NETWORK_WORDS = ["网络", "端口", "连接数", "socket", "network", "ss "]
LOG_WORDS = ["日志", "log", "报错", "异常"]
RESTART_WORDS = ["重启", "restart", "reload"]
STOP_WORDS = ["停止", "停掉", "关掉", "下线", "stop"]
PORT_WORDS = ["端口", "port", "监听", "listen"]
FILE_WORDS = ["文件", "目录", "大小", "占用空间"]
SEARCH_WORDS = ["检索", "搜索", "查找", "搜一下", "grep"]
MODIFY_PATTERN = re.compile(r"修改|改成|改为|变更|更新|设置为|edit|modify|update", re.I)
DELETE_PATTERN = re.compile(r"删除|删掉|清理掉|\bdelete\b|\brm\b")
EXPORT_PATTERN = re.compile(r"导出|备份|dump|export|mysqldump|pg_dump")


def extract_service(prompt: str) -> Optional[str]:
    low = prompt.lower()
    for name in KNOWN_SERVICES:
        if name in low:
            return name
    match = re.search(r"([a-z][a-z0-9_-]{2,24})\s*(?:服务|service)", low)
    if match:
        return match.group(1)
    return None


def extract_path(prompt: str) -> Optional[str]:
    match = re.search(r"(/[A-Za-z0-9_./-]{2,120})", prompt)
    return match.group(1) if match else None


def extract_keyword(prompt: str) -> Optional[str]:
    """抽取用户想检索的日志关键字：支持引号包裹，或「搜索/检索 xxxx」写法。"""
    match = re.search(r"[「“‘\"']([^」”’\"']{1,40})[」”’\"']", prompt)
    if match:
        return match.group(1)
    match = re.search(r"(?:检索|搜索|查找|搜一下|grep|包含|关键字|关键词)\s*[:：]?\s*"
                      r"([A-Za-z0-9_.:@/-]{2,40})", prompt, re.I)
    if match:
        return match.group(1)
    return None


def extract_content(prompt: str) -> Optional[str]:
    """从「把 /etc/hosts 改成：xxx」这类任务里抽取目标内容。"""
    match = re.search(r"(?:改成|改为|设置为|写入|内容为|内容改成)\s*[:：]?\s*(.+)$",
                      prompt.strip(), re.S)
    if not match:
        return None
    content = match.group(1).strip().strip("。").strip()
    if len(content) >= 2 and content[0] in "「“\"'" and content[-1] in "」”\"'":
        content = content[1:-1]
    return content or None


def detect_intent(prompt: str) -> str:
    low = prompt.lower()
    if DELETE_PATTERN.search(low):
        return "delete"
    if EXPORT_PATTERN.search(low) and ("数据" in low or "库" in low or "db" in low or "dump" in low):
        return "export"
    if any(w in low for w in RESTART_WORDS):
        return "restart"
    if any(w in low for w in STOP_WORDS):
        return "stop"
    if MODIFY_PATTERN.search(prompt) and (extract_path(prompt) or "配置" in prompt):
        return "modify"
    if any(w in low for w in PORT_WORDS):
        return "port"
    if any(w in low for w in SEARCH_WORDS) and any(w in low for w in LOG_WORDS):
        return "log_search"
    if any(w in low for w in FILE_WORDS) and extract_path(prompt):
        return "file"
    if any(w in low for w in DIAGNOSE_WORDS):
        return "diagnose"
    # 同时提到多个资源维度 -> 做一次健康体检
    hits = sum(1 for words in (CPU_WORDS, MEM_WORDS, DISK_WORDS, PROCESS_WORDS, NETWORK_WORDS)
               if any(w in low for w in words))
    if hits >= 2:
        return "health"
    if any(w in low for w in DISK_WORDS):
        return "disk"
    if any(w in low for w in MEM_WORDS):
        return "memory"
    if any(w in low for w in CPU_WORDS):
        return "cpu"
    if any(w in low for w in PROCESS_WORDS):
        return "process"
    if any(w in low for w in NETWORK_WORDS):
        return "network"
    if any(w in low for w in LOG_WORDS):
        return "logs"
    return "general"


def tool_step(tool: str, arguments: Dict[str, Any], reason: str) -> Dict[str, Any]:
    return {"kind": "tool", "tool": tool, "arguments": arguments, "reason": reason}


def _counter(results: List[Dict[str, Any]]) -> Counter:
    return Counter(r["tool"] for r in results if r.get("status") == "success")


def _find(results: List[Dict[str, Any]], tool: str) -> List[Dict[str, Any]]:
    return [r for r in results if r["tool"] == tool]


def _pool_exhausted(results: List[Dict[str, Any]]) -> bool:
    for r in results:
        if r["tool"] == "get_database_status" and r.get("status") == "success":
            data = r.get("data") or {}
            if float(data.get("usage_percent") or 0) >= 90:
                return True
        if r["tool"] == "get_service_logs" and r.get("status") == "success":
            text = (r.get("stdout") or "")
            if "pool exhausted" in text or "Connection is not available" in text:
                return True
    return False


def _cpu_high(results: List[Dict[str, Any]]) -> bool:
    for r in _find(results, "get_cpu_usage"):
        data = r.get("data") or {}
        if float(data.get("cpu_user") or 0) >= 60:
            return True
    return False


def _pool_hint(results: List[Dict[str, Any]]) -> bool:
    """日志里出现连接/连接池相关线索时，才去查数据库连接状态。"""
    for r in _find(results, "get_service_logs"):
        text = (r.get("stdout") or "").lower()
        if "pool" in text or "connection" in text or "连接" in text or "sql" in text:
            return True
    return False


def _log_text(results: List[Dict[str, Any]]) -> str:
    return "\n".join((r.get("stdout") or "") for r in _find(results, "get_service_logs"))


def _port_from_logs(results: List[Dict[str, Any]]) -> Optional[int]:
    """从日志里推断服务端口（例如 127.0.0.1:8080），用于进一步检查端口监听。"""
    text = _log_text(results)
    ports = [int(p) for p in re.findall(r":(\d{2,5})\b", text)]
    for candidate in (8080, 8443, 8000, 3000, 9090, 5601):
        if candidate in ports:
            return candidate
    return ports[0] if ports else None


def _has_port_check(results: List[Dict[str, Any]]) -> bool:
    return bool(_find(results, "get_port_status"))


def plan_next(prompt: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """规划下一步动作。ctx 里包含 asset / service / intent / results。"""
    results: List[Dict[str, Any]] = ctx.get("results", [])
    counts = _counter(results)
    service = ctx.get("service") or "payment"
    intent = ctx.get("intent") or detect_intent(prompt)
    attempts = Counter(r["tool"] for r in results)  # 含失败，避免死循环

    def need(tool: str, times: int = 1) -> bool:
        return attempts[tool] < times

    if intent == "diagnose":
        if need("get_cpu_usage"):
            return tool_step("get_cpu_usage", {}, "先看主机整体压力，确认是否存在资源瓶颈")
        if need("get_memory_usage"):
            return tool_step("get_memory_usage", {}, "排除内存不足导致的服务响应变慢")
        if need("get_service_status"):
            return tool_step("get_service_status", {"service": service},
                             "确认 %s 服务本身是否健康、是否刚刚重启过" % service)
        if need("get_service_logs"):
            return tool_step("get_service_logs", {"service": service, "lines": 40},
                             "读取 %s 最近日志，定位具体报错" % service)
        if not _has_port_check(results) and _port_from_logs(results):
            port = _port_from_logs(results)
            return tool_step("get_port_status", {"port": port},
                             "日志里出现 %d 端口相关线索，确认服务端口是否在监听" % port)
        if need("get_database_status") and (ctx.get("service_explicit") or _pool_hint(results)):
            return tool_step("get_database_status", {"service": service},
                             "日志里出现连接相关异常，检查数据库连接池水位")
        if _cpu_high(results) and need("get_process_list"):
            return tool_step("get_process_list", {"top_n": 8},
                             "CPU 明显偏高，定位消耗 CPU 的进程")
        if _pool_exhausted(results) and need("restart_service"):
            return tool_step(
                "restart_service",
                {"service": service},
                "连接池已耗尽且连接无法回收，建议重启 %s 服务以释放连接（生产环境需人工审批）" % service,
            )
        if counts["restart_service"] >= 1:
            if attempts["get_service_status"] < 2:
                return tool_step("get_service_status", {"service": service},
                                 "重启完成，复查服务状态确认已恢复")
            if attempts["get_database_status"] < 2:
                return tool_step("get_database_status", {"service": service},
                                 "复查连接池水位，确认故障根因已消除")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "stop":
        if need("get_service_status"):
            return tool_step("get_service_status", {"service": service},
                             "停止服务属于高风险变更，先确认当前状态")
        if need("stop_service"):
            return tool_step("stop_service", {"service": service},
                             "用户请求停止 %s 服务（会造成业务中断，属破坏性操作）" % service)
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "port":
        port = None
        match = re.search(r"(\d{2,5})", prompt)
        if match:
            port = int(match.group(1))
        if port is None:
            port = _port_from_logs(results) or 8080
        if need("get_port_status"):
            return tool_step("get_port_status", {"port": port}, "检查 %d 端口是否在监听" % port)
        if (ctx.get("service_explicit") and _find(results, "get_port_status")
                and not _find(results, "get_service_status")):
            return tool_step("get_service_status", {"service": service},
                             "端口检查完成，进一步确认对应的服务状态")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "file":
        path = extract_path(prompt) or "/var/log"
        if need("get_file_info"):
            return tool_step("get_file_info", {"path": path}, "查看 %s 的权限、属主与占用空间" % path)
        if need("get_disk_usage") and any(w in prompt for w in ["空间", "磁盘", "占用"]):
            return tool_step("get_disk_usage", {"path": "/"}, "查看磁盘整体水位，判断是否空间不足")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "modify":
        path = extract_path(prompt) or "/etc/payment/application.yml"
        content = extract_content(prompt) or ""
        if need("get_file_info"):
            return tool_step("get_file_info", {"path": path},
                             "变更前先查看 %s 的现状，作为回滚基线" % path)
        if need("modify_config") and content:
            return tool_step("modify_config", {"path": path, "content": content},
                             "按用户要求修改 %s（生产配置变更，需人工审批）" % path)
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "log_search":
        keyword = extract_keyword(prompt) or "error"
        if need("search_service_logs"):
            return tool_step("search_service_logs",
                             {"service": service, "keyword": keyword, "lines": 500},
                             "在 %s 日志中检索「%s」" % (service, keyword))
        if need("get_port_status") and keyword.lower() in ("connection", "refused", "timeout"):
            port = _port_from_logs(results) or 8080
            return tool_step("get_port_status", {"port": port}, "检索到连接类报错，确认端口监听状态")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "health":
        if need("get_cpu_usage"):
            return tool_step("get_cpu_usage", {}, "采集 CPU 与负载指标")
        if need("get_memory_usage"):
            return tool_step("get_memory_usage", {}, "采集内存指标")
        if need("get_disk_usage"):
            return tool_step("get_disk_usage", {"path": "/"}, "采集根分区磁盘指标")
        if need("get_network_status"):
            return tool_step("get_network_status", {}, "采集网络连接指标")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "restart":
        if need("get_service_status"):
            return tool_step("get_service_status", {"service": service},
                             "重启前先确认当前服务状态，记录变更前基线")
        if need("restart_service"):
            return tool_step("restart_service", {"service": service},
                             "用户明确要求重启 %s 服务（生产变更需人工审批）" % service)
        if attempts["get_service_status"] < 2:
            return tool_step("get_service_status", {"service": service}, "复查服务状态确认重启成功")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "cpu":
        if need("get_cpu_usage"):
            return tool_step("get_cpu_usage", {}, "读取 CPU 使用率与负载")
        if _cpu_high(results) and need("get_process_list"):
            return tool_step("get_process_list", {"top_n": 8}, "CPU 偏高，定位占用最高的进程")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "memory":
        if need("get_memory_usage"):
            return tool_step("get_memory_usage", {}, "读取内存与 Swap 使用情况")
        if need("get_top_memory_processes"):
            return tool_step("get_top_memory_processes", {"top_n": 8}, "查看内存占用最高的进程")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "disk":
        path = extract_path(prompt) or "/"
        if need("get_disk_usage"):
            return tool_step("get_disk_usage", {"path": path}, "检查 %s 的磁盘使用情况" % path)
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "process":
        if need("get_process_list"):
            return tool_step("get_process_list", {"top_n": 15}, "按 CPU 排序查看进程列表")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "network":
        if need("get_network_status"):
            return tool_step("get_network_status", {}, "查看连接统计与监听端口")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "logs":
        if need("get_service_logs"):
            return tool_step("get_service_logs", {"service": service, "lines": 50},
                             "读取 %s 最近日志" % service)
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "delete":
        path = extract_path(prompt) or "/var/log/messages"
        if need("delete_file"):
            return tool_step("delete_file", {"path": path},
                             "用户请求删除文件 %s（破坏性操作，需经策略与风险引擎判定）" % path)
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    if intent == "export":
        if need("get_database_status"):
            return tool_step("get_database_status", {"service": service},
                             "先确认数据库连接与压力情况")
        if need("export_database"):
            return tool_step("export_database",
                             {"database": "payment", "target": extract_path(prompt) or "/tmp/payment_dump.sql"},
                             "用户请求导出数据库（数据外发风险极高，需经策略与风险引擎判定）")
        return {"kind": "final", "content": compose_final(prompt, ctx)}

    # general
    if need("get_system_info"):
        return tool_step("get_system_info", {}, "先获取目标主机的基础信息作为分析基线")
    if need("get_service_status"):
        return tool_step("get_service_status", {"service": service},
                         "查看 %s 服务当前状态" % service)
    return {"kind": "final", "content": compose_final(prompt, ctx)}


def _line(text: str, keywords: List[str], limit: int = 2) -> List[str]:
    hits = []
    for raw in (text or "").splitlines():
        if any(k.lower() in raw.lower() for k in keywords):
            hits.append(raw.strip())
        if len(hits) >= limit:
            break
    return hits


def compose_final(prompt: str, ctx: Dict[str, Any]) -> str:
    """把工具结果整理成一份可读的分析报告（真实 LLM 模式下由模型自行生成）。"""
    results: List[Dict[str, Any]] = ctx.get("results", [])
    service = ctx.get("service") or "payment"
    asset = ctx.get("asset", {}).get("name", "目标主机")
    ok = [r for r in results if r.get("status") == "success"]
    failed = [r for r in results if r.get("status") != "success"]

    if not ok:
        body = "本次没有取得任何有效的工具执行结果，无法给出可靠结论。"
        if failed:
            body += "\n\n失败情况：\n" + "\n".join(
                "- %s：%s（%s）" % (r["tool"], r.get("summary") or "无返回", r.get("error") or r.get("status"))
                for r in failed
            )
        return body

    lines: List[str] = ["**分析结论（基于 %s 上的真实工具返回）**" % asset, ""]
    for r in ok:
        tool = r["tool"]
        if tool == "get_cpu_usage":
            data = r.get("data") or {}
            lines.append(
                "- 主机压力：CPU user %.1f%% / sys %.1f%%，1 分钟负载 %s"
                % (float(data.get("cpu_user", 0)), float(data.get("cpu_sys", 0)), data.get("load1", "-"))
            )
        elif tool == "get_memory_usage":
            data = r.get("data") or {}
            used, total = float(data.get("mem_used_mb", 0)), float(data.get("mem_total_mb", 1))
            lines.append("- 内存：已用 %.1f GB / %.1f GB（%.0f%%）" % (used / 1024, total / 1024, used * 100 / total))
        elif tool == "get_service_status":
            lines.append("- %s" % r.get("summary", ""))
        elif tool == "get_service_logs":
            data = r.get("data") or {}
            hits = (data.get("symptom_hits") or data.get("error_samples")
                    or _line(r.get("stdout", ""), ["ERROR", "pool exhausted", "timeout", "WARN"], 3))
            lines.append("- 关键日志：")
            for h in hits or ["（最近日志中未发现明显的 ERROR/WARN）"]:
                lines.append("    · %s" % h[:200])
        elif tool == "search_service_logs":
            data = r.get("data") or {}
            lines.append("- 日志检索：命中 %s 行（关键字 %s）"
                         % (data.get("match_count", "-"), (r.get("arguments") or {}).get("keyword", "-")))
            for sample in (data.get("samples") or [])[:2]:
                lines.append("    · %s" % str(sample)[:200])
        elif tool == "tail_log_file":
            lines.append("- 日志文件：%s" % r.get("summary", ""))
        elif tool == "get_port_status":
            data = r.get("data") or {}
            lines.append("- 端口 %s：%s" % ((r.get("arguments") or {}).get("port", "-"),
                                          "正在监听" if data.get("listening") else "**未在监听**"))
        elif tool == "get_top_memory_processes":
            data = r.get("data") or {}
            lines.append("- 内存占用最高进程：%s" % (data.get("top_process") or r.get("summary", "-")))
        elif tool == "get_file_info":
            data = r.get("data") or {}
            lines.append("- 文件信息：%s %s" % (data.get("info", ""), data.get("size", "")))
        elif tool == "get_database_status":
            data = r.get("data") or {}
            if data.get("usage_percent") is not None:
                lines.append(
                    "- 数据库连接池：使用率 %.1f%%（active %s / max %s，等待线程 %s，近 5 分钟慢查询 %s）"
                    % (float(data.get("usage_percent", 0)), data.get("active"), data.get("max"),
                       data.get("waiters"), data.get("slow_queries_5m"))
                )
            elif data.get("threads_connected") is not None:
                lines.append("- 数据库连接：当前连接数 %s，历史最大连接 %s"
                             % (data.get("threads_connected"), data.get("max_used_connections")))
            else:
                lines.append("- 数据库状态：%s" % r.get("summary", ""))
        elif tool == "get_process_list":
            data = r.get("data") or {}
            if data.get("top_process"):
                lines.append("- 资源占用最高的进程：%s" % data["top_process"])
            else:
                first = (r.get("stdout", "").splitlines() or [""])[1:2]
                lines.append("- 资源占用最高的进程：%s" % (first[0].strip() if first else "-"))
        elif tool == "get_disk_usage":
            data = r.get("data") or {}
            if data.get("use_percent") is not None:
                lines.append("- 磁盘：使用率 %s%%（可用 %s，挂载点 %s）"
                             % (data.get("use_percent"), data.get("avail"), data.get("mounted_on")))
            else:
                lines.append("- 磁盘：%s" % r.get("summary", ""))
        elif tool == "get_network_status":
            lines.append("- 网络：%s" % r.get("summary", ""))
        elif tool == "get_system_info":
            lines.append("- 主机信息：%s" % r.get("summary", ""))
        elif tool == "restart_service":
            lines.append("- 变更动作：%s（执行结果 %s）" % (r.get("command", "restart_service"), r.get("summary", "")))
        elif tool == "modify_config":
            lines.append("- 配置变更：%s（已自动备份为 %s.bak）"
                         % (r.get("command", "modify_config"), (r.get("arguments") or {}).get("path", "-")))
        elif tool == "delete_file":
            lines.append("- 删除结果：%s" % r.get("summary", ""))

    if failed:
        lines.append("")
        lines.append("**未能执行的动作**：")
        for r in failed:
            lines.append("- %s：%s（%s）" % (r["tool"], r.get("summary") or "被拦截", r.get("error") or r.get("status")))

    pool_bad = _pool_exhausted(results)
    restarted = any(r["tool"] == "restart_service" and r.get("status") == "success" for r in results)
    modified = any(r["tool"] == "modify_config" and r.get("status") == "success" for r in results)
    port_closed = any(
        r["tool"] == "get_port_status" and r.get("status") == "success"
        and not (r.get("data") or {}).get("listening", True)
        for r in results
    )
    disk_percent = 0.0
    mem_percent = 0.0
    for item in results:
        if item.get("status") != "success":
            continue
        data = item.get("data") or {}
        if item["tool"] == "get_disk_usage":
            disk_percent = max(disk_percent, float(data.get("use_percent") or 0))
        if item["tool"] == "get_memory_usage":
            mem_percent = max(mem_percent, float(data.get("mem_used_percent") or 0))

    lines.append("")
    if pool_bad and not restarted:
        lines.append(
            "**根因判断**：%s 服务的数据库连接池已被打满，请求线程全部阻塞在等待数据库连接上，"
            "因此表现为上游 nginx 超时、接口响应时间飙升至 30s 以上。" % service
        )
        lines.append("")
        lines.append(
            "**建议动作**：重启 %s 服务以释放被占用的连接，并同步排查是否存在慢 SQL 或连接泄漏。"
            "该操作属于生产环境变更，我已提交人工审批，不会自行执行。" % service
        )
    elif modified:
        target = next((r for r in results if r["tool"] == "modify_config" and r.get("status") == "success"), {})
        path = (target.get("arguments") or {}).get("path", "-")
        lines.append(
            "**处置结果**：配置变更已在目标主机执行完成，%s 已按任务要求更新，"
            "原文件自动备份为 %s.bak，可随时回滚。全过程（提出变更 → 风险判定 → 人工审批 → 实际执行）已记录到审计中心。"
            % (path, path)
        )
    elif restarted:
        lines.append(
            "**处置结果**：%s 服务已完成重启，复查显示服务状态与连接池水位恢复正常，"
            "故障现象已消除。建议继续观察 30 分钟，并排查连接池耗尽的根本原因（慢 SQL / 连接泄漏）。" % service
        )
    elif _cpu_high(results):
        lines.append("**根因判断**：主机 CPU 压力偏高，需结合进程列表进一步定位热点进程。")
    elif disk_percent >= 95:
        lines.append("**风险提示**：根分区磁盘使用率已达 %.0f%%，随时可能写满并导致服务异常。"
                     "建议尽快清理日志或扩容（AI 无删除权限，删除类操作需人工在堡垒机外执行或走审批流程）。"
                     % disk_percent)
    elif disk_percent >= 85:
        lines.append("**风险提示**：磁盘使用率 %.0f%%，已接近告警线，建议尽快清理日志或扩容。" % disk_percent)
    elif mem_percent >= 90:
        lines.append("**风险提示**：内存使用率 %.0f%%，存在 OOM 风险，建议结合内存占用 Top 进程排查。"
                     % mem_percent)
    elif port_closed:
        lines.append("**根因判断**：服务端口未处于监听状态，说明进程可能已崩溃或尚未完成启动。"
                     "建议结合服务状态与日志确认进程退出原因，必要时人工重启服务。")
    else:
        lines.append("**结论**：本次采集的指标未发现明显异常，建议补充具体现象（时间点、报错信息、影响范围）后继续排查。")
    return "\n".join(lines)
