"""把真实 Linux 命令输出解析成结构化指标。

作用：让 AI 面对**真实服务器**时也能拿到和模拟主机一样的结构化数据，
从而给出「CPU user 79.3% / 负载 7.82」「连接池 active=50/50」这种可读结论，
而不是只把一坨 shell 文本丢给模型。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List


def _first(pattern: str, text: str, group: int = 1, cast=float):
    match = re.search(pattern, text or "", re.MULTILINE)
    if not match:
        return None
    try:
        return cast(match.group(group))
    except (TypeError, ValueError):
        return None


def parse(tool_name: str, stdout: str, stderr: str = "") -> Dict[str, Any]:
    text = stdout or ""
    parser = _PARSERS.get(tool_name)
    data = parser(text) if parser else {}
    if stderr and "error" not in data:
        data["stderr"] = stderr[:500]
    return data


# ---------------------------------------------------------------- CPU

def _cpu(text: str) -> Dict[str, Any]:
    """兼容两种真实输出：vmstat（推荐，取第二个采样点）与 top 的 %Cpu(s) 行。"""
    data: Dict[str, Any] = {}

    # 1) vmstat 1 2 | tail -1 → r b swpd free buff cache si so bi bo in cs us sy id wa st
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) >= 17 and all(re.match(r"^-?\d+(\.\d+)?$", f) for f in fields[:17]):
            try:
                data["cpu_user"] = float(fields[12])
                data["cpu_sys"] = float(fields[13])
                data["cpu_idle"] = float(fields[14])
            except (ValueError, IndexError):
                pass
            break

    # 2) top 风格输出（注意 \b，否则 "0 users" 会被误当成 "0 us"）
    if "cpu_user" not in data:
        us = _first(r"([\d.]+)\s*us\b", text)
        sy = _first(r"([\d.]+)\s*sy\b", text)
        idle = _first(r"([\d.]+)\s*id\b", text)
        if us is not None:
            data["cpu_user"] = us
        if sy is not None:
            data["cpu_sys"] = sy
        if idle is not None:
            data["cpu_idle"] = idle

    load1 = _first(r"load average:\s*([\d.]+)", text)
    if load1 is not None:
        data["load1"] = load1

    # 3) 顺带取占用最高的进程（ps 输出第一行数据；用户名开头可排除 vmstat 的纯数字行）
    for line in (text or "").splitlines():
        if re.match(r"^\s*\d+\s+[A-Za-z_][\w.\-]*\s+[\d.]+\s+[\d.]+\s+\S+", line):
            data["top_process"] = line.strip()[:120]
            break
    return data


# ---------------------------------------------------------------- 内存

def _memory(text: str) -> Dict[str, Any]:
    match = re.search(r"Mem:\s+(\d+)\s+(\d+)\s+(\d+)", text or "")
    if not match:
        return {}
    total, used, free = (int(match.group(i)) for i in (1, 2, 3))
    return {
        "mem_total_mb": total,
        "mem_used_mb": used,
        "mem_free_mb": free,
        "mem_used_percent": round(used * 100.0 / total, 1) if total else 0,
    }


# ---------------------------------------------------------------- 磁盘

def _disk(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in (text or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 6 and parts[4].endswith("%"):
            data["use_percent"] = float(parts[4].rstrip("%"))
            data["avail"] = parts[3]
            data["mounted_on"] = parts[-1]
            break
    return data


# ---------------------------------------------------------------- 服务

def _service_status(text: str) -> Dict[str, Any]:
    status = None
    if re.search(r"Active:\s*active\s*\(running\)", text or ""):
        status = "running"
    elif re.search(r"Active:\s*active\s*\(exited\)", text or ""):
        status = "exited"
    elif re.search(r"Active:\s*failed", text or ""):
        status = "failed"
    elif re.search(r"Active:\s*inactive", text or ""):
        status = "stopped"
    data: Dict[str, Any] = {}
    if status:
        data["status"] = status
    since = _first(r"since\s+([^;]+);", text, cast=str)
    if since:
        data["since"] = since.strip()
    pid = _first(r"Main PID:\s*(\d+)", text, cast=int)
    if pid:
        data["pid"] = pid
    memory = _first(r"Memory:\s*([\d.]+[KMG]?)", text, cast=str)
    if memory:
        data["memory"] = memory
    if re.search(r"could not be found|not-found", text or "", re.I):
        data["status"] = "not-found"
    return data


# ---------------------------------------------------------------- 日志

def _logs(text: str) -> Dict[str, Any]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    errors = [line for line in lines if re.search(r"\b(ERROR|FATAL|CRITICAL)\b", line)]
    warns = [line for line in lines if re.search(r"\bWARN(ING)?\b", line)]
    keywords = ["pool exhausted", "connection is not available", "timeout", "timed out",
                "refused", "oom", "no space left", "too many open files"]
    hits = [line for line in lines if any(k in line.lower() for k in keywords)]
    return {
        "lines": len(lines),
        "error_count": len(errors),
        "warn_count": len(warns),
        "error_samples": errors[:3],
        "symptom_hits": hits[:3],
    }


def _log_search(text: str) -> Dict[str, Any]:
    matches = [line for line in (text or "").splitlines() if line.strip()]
    return {"match_count": len(matches), "samples": matches[:5]}


# ---------------------------------------------------------------- 进程 / 端口

def _processes(text: str) -> Dict[str, Any]:
    rows: List[str] = []
    for line in (text or "").splitlines()[1:]:
        if line.strip():
            rows.append(line.strip())
    top = None
    if rows:
        parts = rows[0].split(None, 5)
        if len(parts) >= 4:
            top = "%s(pid=%s, cpu=%s%%, mem=%s%%)" % (
                parts[5][:60] if len(parts) > 5 else parts[3], parts[0], parts[2], parts[3])
    return {"process_count": len(rows), "top_process": top}


def _port(text: str) -> Dict[str, Any]:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return {"listening": bool(lines), "listeners": lines[:3]}


def _file_info(text: str) -> Dict[str, Any]:
    first = (text or "").strip().splitlines()
    return {"info": first[0][:200] if first else "", "size": first[1][:40] if len(first) > 1 else ""}


# ---------------------------------------------------------------- 数据库

def _database(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    threads = _first(r"Threads_connected\s+(\d+)", text, cast=int)
    if threads is not None:
        data["threads_connected"] = threads
    max_used = _first(r"Max_used_connections\s+(\d+)", text, cast=int)
    if max_used is not None:
        data["max_used_connections"] = max_used
    return data


_PARSERS = {
    "get_cpu_usage": _cpu,
    "get_system_info": _cpu,
    "get_memory_usage": _memory,
    "get_disk_usage": _disk,
    "get_service_status": _service_status,
    "get_service_logs": _logs,
    "search_service_logs": _log_search,
    "tail_log_file": _logs,
    "get_process_list": _processes,
    "get_top_memory_processes": _processes,
    "get_port_status": _port,
    "get_file_info": _file_info,
    "get_database_status": _database,
}
