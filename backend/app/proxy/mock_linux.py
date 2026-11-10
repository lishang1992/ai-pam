"""演示用模拟 Linux 主机。

目的：让整套闭环在没有真实 Linux 服务器、没有大模型 API 的机器上也能完整演示。
其中 payment 服务被预置为「数据库连接池耗尽」的故障场景，
重启后会恢复健康 —— 因此 Demo 的「执行前 / 执行后」对比是真实发生的状态变化。
"""

from __future__ import annotations

import random
from typing import Any, Dict, List

PAYMENT_BAD_LOGS = [
    "2026-09-15 15:28:41.221 WARN  [http-nio-8080-exec-17] c.z.payment.PaymentController - query order timeout, cost=30012ms, orderId=SO20260915-88213",
    "2026-09-15 15:29:02.914 ERROR [http-nio-8080-exec-21] c.z.payment.OrderService - Cannot get a connection, pool exhausted, active=50, idle=0, waiters=37",
    "2026-09-15 15:29:03.115 ERROR [hikari-pool-3] com.zaxxer.hikari.pool.HikariPool - HikariPool-1 - Connection is not available, request timed out after 30000ms",
    "2026-09-15 15:29:41.007 WARN  [scheduling-1] c.z.payment.JobRunner - slow query detected: SELECT * FROM t_order WHERE status = ? and created_at > ? , cost=28411ms",
    "2026-09-15 15:30:12.331 ERROR [http-nio-8080-exec-33] c.z.payment.OrderService - Cannot get a connection, pool exhausted, active=50, idle=0, waiters=52",
    "2026-09-15 15:31:05.882 WARN  [http-nio-8080-exec-08] c.z.payment.PaymentController - query order timeout, cost=30028ms, orderId=SO20260915-88344",
]

PAYMENT_GOOD_LOGS = [
    "2026-09-15 15:33:02.104 INFO  [main] c.z.payment.PaymentApplication - Starting PaymentApplication v2.8.1",
    "2026-09-15 15:33:04.559 INFO  [main] com.zaxxer.hikari.HikariDataSource - HikariPool-1 - Start completed. poolSize=50, active=6, idle=44",
    "2026-09-15 15:33:05.871 INFO  [main] c.z.payment.PaymentApplication - Started PaymentApplication in 8.412 seconds",
    "2026-09-15 15:33:09.220 INFO  [http-nio-8080-exec-2] c.z.payment.PaymentController - query order success, cost=48ms, orderId=SO20260915-88390",
]

NGINX_LOGS = [
    "2026-09-15 15:30:01 [warn] 1182#0: *88213 upstream response is buffered to a temporary file /var/cache/nginx/proxy_temp/3/07/0000000073",
    "2026-09-15 15:30:41 [error] 1182#0: *88214 upstream timed out (110: Connection timed out) while reading response header from upstream, client: 10.20.31.7, request: \"POST /api/pay/create HTTP/1.1\", upstream: \"http://127.0.0.1:8080/api/pay/create\"",
    "2026-09-15 15:31:22 [warn] 1182#0: 512 worker_connections are not enough",
]


class MockLinuxHost:
    """一台模拟 Linux 主机的状态机。"""

    def __init__(self, asset: Dict[str, Any]):
        self.asset = asset
        self.name = asset["name"]
        self.hostname = asset["name"].lower().replace(" ", "-")
        production = asset.get("env_tag") == "production"
        self.degraded_payment = production
        self.os_name = "Rocky Linux 9.4 (Blue Onyx)" if production else "Ubuntu 22.04.4 LTS"
        self.kernel = "5.14.0-427.28.1.el9_4.x86_64" if production else "5.15.0-107-generic"
        self.cores = 8 if production else 4
        self.uptime_seconds = 41 * 86400 + 7 * 3600
        self.files: Dict[str, str] = {
            "/etc/nginx/nginx.conf": "worker_processes auto;\nworker_connections 512;\n",
            "/etc/payment/application.yml": "server:\n  port: 8080\nspring:\n  datasource:\n    hikari:\n      maximum-pool-size: 50\n",
        }
        self.installed_packages: List[str] = []
        self.reset()

    # ------------------------------------------------------------ 状态
    def reset(self) -> None:
        degraded = self.degraded_payment
        self.services: Dict[str, Dict[str, Any]] = {
            "payment": {
                "status": "running",
                "pid": 20871,
                "cpu": 78.4 if degraded else 6.2,
                "mem_mb": 1832 if degraded else 962,
                "since": "2026-09-15 09:12:41 CST",
                "restarts": 1,
            },
            "nginx": {
                "status": "running",
                "pid": 1182,
                "cpu": 3.1 if degraded else 1.4,
                "mem_mb": 214,
                "since": "2026-09-01 02:00:07 CST",
                "restarts": 0,
            },
            "mysqld": {
                "status": "running",
                "pid": 991,
                "cpu": 41.7 if degraded else 5.8,
                "mem_mb": 4096,
                "since": "2026-09-01 02:00:19 CST",
                "restarts": 0,
            },
            "redis": {
                "status": "running",
                "pid": 1337,
                "cpu": 1.2,
                "mem_mb": 328,
                "since": "2026-09-01 02:00:22 CST",
                "restarts": 0,
            },
        }
        self.logs: Dict[str, List[str]] = {
            "payment": list(PAYMENT_BAD_LOGS if degraded else PAYMENT_GOOD_LOGS),
            "nginx": list(NGINX_LOGS) if degraded else [
                "2026-09-15 15:30:00 [notice] 1182#0: signal process started",
            ],
            "mysqld": [
                "2026-09-15 15:2%d:00 [Note] InnoDB: Buffer pool(s) load completed" % (i % 10)
                for i in range(8)
            ],
            "redis": ["2026-09-15 15:25:00 * Ready to accept connections"],
        }
        self.pools: Dict[str, Dict[str, Any]] = {
            "payment": {
                "pool": "HikariPool-1",
                "max": 50,
                "active": 50 if degraded else 6,
                "idle": 0 if degraded else 44,
                "waiters": 52 if degraded else 0,
                "slow_queries_5m": 41 if degraded else 0,
                "threads_connected": 148 if degraded else 37,
            }
        }

    def _degraded_now(self) -> bool:
        return self.pools["payment"]["active"] >= self.pools["payment"]["max"]

    def _restart(self, service: str) -> None:
        svc = self.services.get(service)
        if svc is None:
            return
        svc["status"] = "running"
        svc["pid"] = random.randint(20000, 40000)
        svc["restarts"] = int(svc.get("restarts", 0)) + 1
        svc["since"] = "刚刚（本次会话内）"
        if service == "payment":
            svc["cpu"] = 5.8
            svc["mem_mb"] = 964
            pool = self.pools["payment"]
            pool.update({"active": 6, "idle": 44, "waiters": 0, "slow_queries_5m": 0, "threads_connected": 41})
            self.logs["payment"] = list(PAYMENT_GOOD_LOGS)
        else:
            svc["cpu"] = max(1.0, float(svc.get("cpu", 5)) / 4)

    # ------------------------------------------------------------ 工具实现
    def _out(self, stdout: str, summary: str = "", **extra) -> Dict[str, Any]:
        data = {"stdout": stdout, "exit_code": 0, "summary": summary}
        data.update(extra)
        return data

    def get_system_info(self) -> Dict[str, Any]:
        days = self.uptime_seconds // 86400
        hours = (self.uptime_seconds % 86400) // 3600
        load = "7.82, 6.91, 4.35" if self._degraded_now() else "0.42, 0.55, 0.61"
        stdout = (
            " 15:32:01 up %d days, %2d:%02d,  2 users,  load average: %s\n"
            "NAME=\"%s\"\nVERSION=\"9.4 (Blue Onyx)\"\nID=\"rocky\"\n"
            "%s\n%d\n"
            % (days, hours, 12, load, self.os_name.split(" (")[0], self.kernel, self.cores)
        )
        return self._out(
            stdout,
            "主机 %s 运行 %d 天，负载 %s" % (self.hostname, days, load.split(",")[0]),
            data={"hostname": self.hostname, "load1": float(load.split(",")[0])},
        )

    def get_cpu_usage(self) -> Dict[str, Any]:
        degraded = self._degraded_now()
        us = 79.3 if degraded else 8.4
        sy = 12.1 if degraded else 2.1
        idle = max(0.0, 100 - us - sy)
        top = (
            "  PID USER      PR  NI    VIRT    RES    SHR S  %%CPU %%MEM     TIME+ COMMAND\n"
            "20871 payadm    20   0 4821092 1.832g  21408 S  %5.1f  11.6  41:22.81 java\n"
            "  991 mysql     20   0 6120448 4.001g  33112 S  %5.1f  25.4  18:04.32 mysqld\n"
            " 1182 root      20   0  742188  21432   9120 S   2.1   0.1   0:44.10 nginx\n"
            % (48.2 if degraded else 4.1, 31.4 if degraded else 2.8)
        )
        stdout = (
            "top - 15:32:02 up 41 days, 23:12,  2 users,  load average: %s\n"
            "Cpu(s): %4.1f us, %4.1f sy,  0.0 ni, %5.1f id,  0.0 wa\n"
            "%s" % ("7.82, 6.91, 4.35" if degraded else "0.42, 0.55, 0.61", us, sy, idle, top)
        )
        return self._out(
            stdout,
            "CPU 使用率偏高：user %.1f%% + sys %.1f%%" % (us, sy),
            data={"cpu_user": us, "cpu_sys": sy, "load1": 7.82 if degraded else 0.42},
        )

    def get_memory_usage(self) -> Dict[str, Any]:
        degraded = self._degraded_now()
        used = 14208 if degraded else 6904
        total = 16384
        stdout = (
            "              total        used        free      shared  buff/cache   available\n"
            "Mem:          16384       %5d        1024         212        1152        %5d\n"
            "Swap:          4096         %4d         %5d\n"
            % (used, max(512, total - used - 900), 3072 if degraded else 12,
               1024 if degraded else 4084)
        )
        return self._out(
            stdout,
            "内存使用 %.1f%%（used %dMB / total %dMB）" % (used * 100.0 / total, used, total),
            data={"mem_used_mb": used, "mem_total_mb": total},
        )

    def get_process_list(self, top_n: int = 10, filter: str = "") -> Dict[str, Any]:
        degraded = self._degraded_now()
        rows = [
            (20871, "payadm", "%5.1f" % (48.2 if degraded else 4.1), "11.6", "41:22.81", "java -jar /opt/payment/payment.jar"),
            (991, "mysql", "%5.1f" % (31.4 if degraded else 2.8), "25.4", "18:04.32", "/usr/sbin/mysqld"),
            (1182, "root", "2.1", "0.1", "0:44.10", "nginx: worker process"),
            (1337, "redis", "1.2", "2.0", "3:11.02", "/usr/bin/redis-server 127.0.0.1:6379"),
            (1, "root", "0.0", "0.1", "1:22.11", "/usr/lib/systemd/systemd"),
        ]
        if filter:
            rows = [r for r in rows if filter.lower() in r[5].lower()] or rows[:1]
        head = "  PID USER     %CPU %MEM     TIME+ COMMAND\n"
        body = "\n".join(
            "%5d %-8s %5s %5s %9s %s" % (r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows[:top_n]
        )
        return self._out(head + body, "返回 %d 条进程" % min(top_n, len(rows)))

    def get_disk_usage(self, path: str = "/") -> Dict[str, Any]:
        rows = [
            ("/dev/mapper/root", "/", "100G", "68G", "32G", "69%"),
            ("/dev/nvme0n1p2", "/var", "400G", "312G", "88G", "78%"),
            ("/dev/nvme0n1p3", "/data", "1.8T", "1.1T", "700G", "61%"),
        ]
        out = ["Filesystem                 Size  Used Avail Use% Mounted on"]
        for r in rows:
            text = "%-24s %5s %5s %5s %4s %s" % (r[0], r[2], r[3], r[4], r[5], r[1])
            if r[1] == path or path in ("/", ""):
                out.append(text)
        return self._out("\n".join(out), "磁盘 %s 使用率正常" % path)

    def get_network_status(self) -> Dict[str, Any]:
        out = (
            "Total: 412\nTCP:   318 (estab 214, closed 62, orphaned 0, timewait 41)\n\n"
            "State  Recv-Q Send-Q Local Address:Port  Peer Address:Port Process\n"
            "LISTEN 0      511    0.0.0.0:80         0.0.0.0:*     users:((\"nginx\",pid=1182))\n"
            "LISTEN 0      128    0.0.0.0:8080       0.0.0.0:*     users:((\"java\",pid=20871))\n"
            "LISTEN 0      128    127.0.0.1:3306     0.0.0.0:*     users:((\"mysqld\",pid=991))\n"
            "LISTEN 0      128    127.0.0.1:6379     0.0.0.0:*     users:((\"redis\",pid=1337))\n"
        )
        return self._out(out, "TCP 连接 318 条，其中 ESTABLISHED 214 条")

    def get_service_status(self, service: str) -> Dict[str, Any]:
        svc = self.services.get(service)
        if svc is None:
            return {"stdout": "Unit %s.service could not be found.\n" % service, "exit_code": 4,
                    "summary": "未找到该服务", "error": "服务不存在"}
        stdout = (
            "● %s.service - %s Service\n"
            "     Loaded: loaded (/usr/lib/systemd/system/%s.service; enabled; preset: disabled)\n"
            "     Active: %s (running) since %s; %s ago\n"
            "   Main PID: %d (java)\n"
            "      Tasks: 74 (limit: 20148)\n"
            "     Memory: %.1fM\n"
            "        CPU: %s\n"
            % (service, service.capitalize(), service, svc["status"], svc["since"], "3h 12min",
               svc["pid"], svc["mem_mb"], "%s%%" % svc["cpu"])
        )
        return self._out(
            stdout,
            "服务 %s 状态 %s，CPU %s%%，内存 %.0fMB，重启次数 %d"
            % (service, svc["status"], svc["cpu"], svc["mem_mb"], svc["restarts"]),
            data={"service": service, "status": svc["status"]},
        )

    def get_service_logs(self, service: str, lines: int = 50) -> Dict[str, Any]:
        rows = self.logs.get(service)
        if rows is None:
            return {"stdout": "", "exit_code": 1, "summary": "无日志", "error": "未找到该服务日志"}
        shown = rows[-int(lines):]
        return self._out(
            "-- Logs begin for %s --\n" % service + "\n".join(shown),
            "读取 %s 最近 %d 行日志" % (service, len(shown)),
            data={"service": service, "lines": len(shown)},
        )

    def get_database_status(self, service: str) -> Dict[str, Any]:
        pool = self.pools.get(service)
        if pool is None:
            return {"stdout": "", "exit_code": 1, "summary": "无数据库连接信息",
                    "error": "该服务未配置数据库连接池"}
        usage = pool["active"] * 100.0 / pool["max"]
        stdout = (
            "Variable_name            Value\n"
            "Threads_connected        %d\n"
            "Max_used_connections     %d\n"
            "%s pool=%s  max=%d active=%d idle=%d waiters=%d  slow_queries_5m=%d\n"
            % (pool["threads_connected"], pool["max"], pool["pool"], pool["pool"],
               pool["max"], pool["active"], pool["idle"], pool["waiters"], pool["slow_queries_5m"])
        )
        return self._out(
            stdout,
            "%s 连接池使用率 %.0f%%（active=%d / max=%d，等待线程 %d）"
            % (service, usage, pool["active"], pool["max"], pool["waiters"]),
            data={
                "active": pool["active"], "max": pool["max"], "idle": pool["idle"],
                "waiters": pool["waiters"], "usage_percent": round(usage, 1),
                "slow_queries_5m": pool["slow_queries_5m"],
            },
        )

    def search_service_logs(self, service: str, keyword: str, lines: int = 500) -> Dict[str, Any]:
        rows = self.logs.get(service)
        if rows is None:
            return {"stdout": "", "exit_code": 1, "summary": "无日志", "error": "未找到该服务日志"}
        matched = [line for line in rows if keyword.lower() in line.lower()]
        text = "\n".join(matched) if matched else "（未匹配到关键字 %s）" % keyword
        return self._out(
            text,
            "日志检索「%s」命中 %d 行" % (keyword, len(matched)),
            data={"match_count": len(matched), "samples": matched[:5]},
        )

    def tail_log_file(self, path: str, lines: int = 100) -> Dict[str, Any]:
        rows = list(self.logs.get("payment", [])) if path.endswith("messages") or "payment" in path else [
            "%s 的最近日志（模拟）" % path,
        ]
        shown = rows[-int(lines):]
        text = "\n".join(shown)
        errors = [line for line in shown if "ERROR" in line or "WARN" in line]
        return self._out(
            text,
            "读取 %s 最近 %d 行" % (path, len(shown)),
            data={"lines": len(shown), "error_count": len(errors),
                  "error_samples": errors[:3], "symptom_hits": []},
        )

    def get_port_status(self, port: int) -> Dict[str, Any]:
        listeners = {
            80: 'LISTEN 0 511 0.0.0.0:80 0.0.0.0:* users:(("nginx",pid=1182,fd=6))',
            8080: 'LISTEN 0 128 0.0.0.0:8080 0.0.0.0:* users:(("java",pid=%d,fd=42))'
                  % self.services["payment"]["pid"],
            3306: 'LISTEN 0 128 127.0.0.1:3306 0.0.0.0:* users:(("mysqld",pid=991,fd=21))',
            6379: 'LISTEN 0 128 127.0.0.1:6379 0.0.0.0:* users:(("redis-server",pid=1337,fd=6))',
            22: 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=880,fd=3))',
        }
        line = listeners.get(int(port))
        if line:
            return self._out(line, "端口 %d 正在监听" % port, data={"listening": True, "listeners": [line]})
        return self._out(
            "port %d is NOT listening" % port,
            "端口 %d 没有监听进程" % port,
            data={"listening": False, "listeners": []},
        )

    def get_top_memory_processes(self, top_n: int = 10) -> Dict[str, Any]:
        degraded = self._degraded_now()
        rows = [
            (991, "mysql", "25.4", "31.4", 4195328, "/usr/sbin/mysqld"),
            (20871, "payadm", "11.6", "48.2" if degraded else "4.1", 1920712, "java -jar /opt/payment/payment.jar"),
            (1337, "redis", "2.0", "1.2", 336384, "/usr/bin/redis-server 127.0.0.1:6379"),
            (1182, "root", "0.1", "2.1", 21932, "nginx: worker process"),
        ]
        head = "  PID USER     %MEM %CPU   RSS COMMAND\n"
        body = "\n".join(
            "%5d %-8s %5s %5s %6d %s" % (r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows[:int(top_n)]
        )
        top = rows[0]
        return self._out(
            head + body,
            "内存占用最高：%s（pid=%d，%s%%）" % (top[5].split()[0], top[0], top[2]),
            data={"process_count": min(int(top_n), len(rows)),
                  "top_process": "%s(pid=%d, mem=%s%%)" % (top[5].split()[0], top[0], top[2])},
        )

    def get_file_info(self, path: str) -> Dict[str, Any]:
        if path in self.files or path.startswith("/var/log"):
            size = "%dK" % max(4, len(self.files.get(path, "")) * 2)
            stdout = (
                "-rw-r--r--. 1 root root %s Sep 15 15:30 %s\n%s\t%s"
                % (len(self.files.get(path, "")), path, size, path)
            )
            return self._out(stdout, "%s 大小 %s" % (path, size), data={"info": path, "size": size})
        stdout = 'ls: cannot access \'%s\': No such file or directory' % path
        return {"stdout": stdout, "exit_code": 2, "summary": "文件不存在", "error": "No such file or directory"}

    def restart_service(self, service: str) -> Dict[str, Any]:
        if service not in self.services:
            return {"stdout": "", "exit_code": 5, "summary": "服务不存在", "error": "Unit not found"}
        self._restart(service)
        stdout = (
            "Restarting %s.service...\n"
            "Warning: The unit file, source configuration file or drop-ins of %s.service changed on disk.\n"
            "Active: active (running) since %s\n"
            % (service, service, self.services[service]["since"])
        )
        return self._out(
            stdout,
            "服务 %s 已重启完成，PID %d" % (service, self.services[service]["pid"]),
            data={"service": service, "status": "running", "pid": self.services[service]["pid"]},
        )

    def start_service(self, service: str) -> Dict[str, Any]:
        if service not in self.services:
            return {"stdout": "", "exit_code": 5, "summary": "服务不存在", "error": "Unit not found"}
        self.services[service]["status"] = "running"
        return self._out("Started %s.service" % service, "服务 %s 已启动" % service,
                         data={"service": service, "status": "running"})

    def stop_service(self, service: str) -> Dict[str, Any]:
        if service not in self.services:
            return {"stdout": "", "exit_code": 5, "summary": "服务不存在", "error": "Unit not found"}
        self.services[service]["status"] = "stopped"
        return self._out("Stopped %s.service" % service, "服务 %s 已停止" % service,
                         data={"service": service, "status": "stopped"})

    def modify_config(self, path: str, content: str) -> Dict[str, Any]:
        existed = path in self.files
        self.files[path] = content
        return self._out(
            "backup written: %s.bak\ntarget updated: %s" % (path, path),
            "配置 %s 已更新（原文件备份为 %s.bak）" % (path, path),
            data={"path": path, "backup": "%s.bak" % path, "created": not existed},
        )

    def install_package(self, package: str) -> Dict[str, Any]:
        self.installed_packages.append(package)
        return self._out(
            "Last metadata expiration check: 0:12:41 ago.\n"
            "Dependencies resolved.\nInstalled:\n  %s-1.2.3-1.el9.x86_64\nComplete!" % package,
            "软件包 %s 安装完成" % package,
        )

    def delete_file(self, path: str) -> Dict[str, Any]:
        existed = path in self.files
        self.files.pop(path, None)
        return self._out("", "文件 %s %s" % (path, "已删除" if existed else "不存在，rm -f 静默返回"),
                         data={"path": path, "existed": existed})

    def export_database(self, database: str, target: str) -> Dict[str, Any]:
        return self._out(
            "-- MySQL dump 10.13  Distrib 8.0.36\n-- Host: localhost    Database: %s\n" % database,
            "数据库 %s 已导出到 %s" % (database, target),
            data={"database": database, "target": target},
        )


_HOSTS: Dict[str, MockLinuxHost] = {}


def get_host(asset: Dict[str, Any]) -> MockLinuxHost:
    key = asset["name"]
    if key not in _HOSTS:
        _HOSTS[key] = MockLinuxHost(asset)
    return _HOSTS[key]


def reset_all() -> None:
    for host in _HOSTS.values():
        host.reset()


def call(asset: Dict[str, Any], tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    host = get_host(asset)
    fn = getattr(host, tool_name, None)
    if fn is None:
        return {"stdout": "", "exit_code": 1, "summary": "",
                "error": "模拟主机未实现该工具：%s" % tool_name}
    return fn(**arguments)
