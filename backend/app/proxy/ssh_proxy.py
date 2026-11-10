"""执行代理：AI 发出的工具调用，最终只能经过这里落到目标主机。

三种执行器：
  * mock  —— 演示用模拟主机（默认，无需真实 Linux）
  * ssh   —— 真实 Linux。命令来自工具模板（不允许 AI 自由拼 shell）：
               · 优先使用 paramiko（支持口令 / 私钥，安装： pip install paramiko）
               · 未安装 paramiko 时回退到系统 OpenSSH 客户端（私钥免密登录）
  * local —— 本机执行，便于在没有跳板机的环境里做联调

安全约定：
  1. 参数已由 Tool Registry 做类型 + 正则白名单校验；
  2. 命令由固定模板渲染，AI 无法注入任意命令；
  3. 口令/私钥只从环境变量读取，绝不写入数据库。
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict

from ..settings import SSH_COMMAND_TIMEOUT, SSH_CONNECT_TIMEOUT
from ..security import secrets
from . import mock_linux, parsers


class ExecutionError(RuntimeError):
    pass


def _load_private_key(path: str):
    """按 Ed25519 → ECDSA → RSA 依次尝试，覆盖常见的 Linux 运维密钥类型。"""
    import paramiko

    key_path = os.path.expanduser(path)
    if not os.path.exists(key_path):
        raise ExecutionError("私钥文件不存在：%s" % key_path)
    errors = []
    for key_type in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey):
        try:
            return key_type.from_private_key_file(key_path)
        except Exception as exc:  # 换下一种密钥类型继续尝试
            errors.append("%s: %s" % (key_type.__name__, str(exc)[:80]))
    raise ExecutionError("无法解析私钥 %s（%s）。如私钥有口令，请改用免密密钥或 "
                         "AIB_SSH_PASSWORD_* 环境变量。" % (key_path, "; ".join(errors)))


def _run_ssh(asset: Dict[str, Any], command: str) -> Dict[str, Any]:
    try:
        import paramiko
    except ImportError:
        return _run_ssh_cli(asset, command)

    credential_ref = asset.get("credential_ref") or ""
    if asset.get("auth_type") == "key":
        pkey = _load_private_key(credential_ref or "~/.ssh/id_rsa")
        password = None
    else:
        secret = secrets.get(credential_ref) or ""
        pkey = None
        password = secret or None
        if not password:
            raise ExecutionError(
                "资产 %s 未配置口令（环境变量或本地凭据库：%s），拒绝连接"
                % (asset["name"], credential_ref or "<empty>")
            )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=asset["host"],
        port=int(asset.get("port") or 22),
        username=asset.get("ssh_user") or "root",
        password=password,
        pkey=pkey,
        timeout=SSH_CONNECT_TIMEOUT,
        banner_timeout=SSH_CONNECT_TIMEOUT,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=SSH_COMMAND_TIMEOUT)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
    finally:
        client.close()
    return {
        "stdout": out,
        "stderr": err,
        "exit_code": code,
        "summary": (out.strip().splitlines() or [""])[-1][:200] if out.strip() else err.strip()[:200],
        "error": err.strip() if code != 0 else "",
        "command": command,
        "transport": "paramiko",
    }


def _run_ssh_cli(asset: Dict[str, Any], command: str) -> Dict[str, Any]:
    """回退方案：使用系统自带的 ssh 客户端（仅支持私钥免密登录）。"""
    key_path = os.path.expanduser(asset.get("credential_ref") or "~/.ssh/id_rsa")
    if asset.get("auth_type") == "password":
        raise ExecutionError(
            "资产 %s 使用口令认证，需要安装 paramiko：pip install paramiko" % asset["name"]
        )
    argv = [
        "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=%d" % SSH_CONNECT_TIMEOUT,
        "-i", key_path, "-p", str(asset.get("port") or 22),
        "%s@%s" % (asset.get("ssh_user") or "root", asset["host"]),
        command,
    ]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=SSH_COMMAND_TIMEOUT)
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "summary": (proc.stdout.strip().splitlines() or [""])[-1][:200] if proc.stdout.strip()
        else proc.stderr.strip()[:200],
        "error": proc.stderr.strip() if proc.returncode != 0 else "",
        "command": command,
        "transport": "openssh-cli",
    }


def _run_local(command: str) -> Dict[str, Any]:
    proc = subprocess.run(
        command, shell=True, capture_output=True, text=True, timeout=SSH_COMMAND_TIMEOUT
    )
    return {
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "exit_code": proc.returncode,
        "summary": (proc.stdout.strip().splitlines() or [""])[-1][:200] if proc.stdout.strip()
        else proc.stderr.strip()[:200],
        "error": proc.stderr.strip() if proc.returncode != 0 else "",
        "command": command,
    }


def execute(
    asset: Dict[str, Any],
    tool: Dict[str, Any],
    arguments: Dict[str, Any],
    command: str,
) -> Dict[str, Any]:
    """按资产的执行器类型执行工具，返回统一结构。"""
    executor = (asset.get("executor") or "mock").lower()
    started = time.time()

    if executor == "mock":
        result = mock_linux.call(asset, tool["name"], arguments)
        result.setdefault("command", command)
    elif executor == "ssh":
        result = _run_ssh(asset, command)
    elif executor == "local":
        result = _run_local(command)
    else:
        raise ExecutionError("未知执行器类型：%s" % executor)

    result["executor"] = executor
    result["target"] = asset.get("name")
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    result["command"] = result.get("command") or command
    # 真实主机：把 shell 输出解析成结构化指标，供 AI 分析与审计使用
    if executor in ("ssh", "local") and not result.get("data"):
        result["data"] = parsers.parse(tool["name"], result.get("stdout", ""), result.get("stderr", ""))
    if not result.get("summary"):
        result["summary"] = _summarize(tool["name"], result.get("data") or {}, result.get("stdout", ""))
    return result


def _summarize(tool_name: str, data: Dict[str, Any], stdout: str) -> str:
    if tool_name in ("get_cpu_usage", "get_system_info") and data.get("cpu_user") is not None:
        return "CPU user %.1f%% / sys %.1f%%，负载 %s" % (
            float(data.get("cpu_user") or 0), float(data.get("cpu_sys") or 0), data.get("load1", "-"))
    if tool_name == "get_memory_usage" and data.get("mem_total_mb"):
        return "内存使用 %.1f%%（used %sMB / total %sMB）" % (
            float(data.get("mem_used_percent") or 0), data.get("mem_used_mb"), data.get("mem_total_mb"))
    if tool_name == "get_service_status" and data.get("status"):
        return "服务状态 %s%s" % (data["status"], ("，since " + data["since"]) if data.get("since") else "")
    if tool_name == "get_service_logs" and data.get("lines") is not None:
        return "读取日志 %s 行，ERROR %s 条 / WARN %s 条" % (
            data.get("lines"), data.get("error_count"), data.get("warn_count"))
    if tool_name == "search_service_logs" and data.get("match_count") is not None:
        return "日志检索命中 %s 行" % data.get("match_count")
    if tool_name == "get_port_status":
        return "端口%s" % ("正在监听" if data.get("listening") else "未监听")
    if tool_name == "get_disk_usage" and data.get("use_percent") is not None:
        return "磁盘使用率 %s%%（可用 %s）" % (data.get("use_percent"), data.get("avail", "-"))
    return (stdout or "").strip().splitlines()[-1][:200] if (stdout or "").strip() else ""


def test_connection(asset: Dict[str, Any]) -> Dict[str, Any]:
    """资产连通性测试：ssh 执行器会真的连上去跑一次 uptime。"""
    executor = (asset.get("executor") or "mock").lower()
    started = time.time()
    if executor == "mock":
        return {"ok": True, "latency_ms": int((time.time() - started) * 1000),
                "message": "模拟主机（演示执行器）可用，无需真实连接",
                "stdout": "15:32:01 up 41 days,  2 users,  load average: 7.82, 6.91, 4.35"}
    if executor == "local":
        result = _run_local("uptime && uname -r")
    else:
        if not asset.get("host"):
            return {"ok": False, "message": "未填写主机地址"}
        try:
            result = _run_ssh(asset, "uptime && uname -r && nproc")
        except ExecutionError as exc:
            return {"ok": False, "message": str(exc)}
        except Exception as exc:  # paramiko 的各种网络异常统一转成可读信息
            return {"ok": False, "message": "SSH 连接失败：%s" % str(exc)[:300]}
    return {
        "ok": int(result.get("exit_code", 1)) == 0 and not result.get("error"),
        "latency_ms": int((time.time() - started) * 1000),
        "message": "连接成功" if int(result.get("exit_code", 1)) == 0 else (result.get("error") or "连接失败"),
        "stdout": result.get("stdout", ""),
        "transport": result.get("transport", executor),
    }


def check_command_safety(command: str) -> None:
    """兜底防线：即便模板被改坏，也拦住明显危险的 shell 组合。"""
    banned = [";", "&&", "||", "`", "$(", "|sh", "> /dev/sd"]
    for token in banned:
        if token in command and token not in ("&&", "||"):
            raise ExecutionError("命令中出现了被禁止的 shell 组合：%s" % token)
