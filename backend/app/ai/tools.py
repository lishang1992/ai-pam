"""工具注册表：从 config/tools.yaml 载入工具定义，并落到数据库。"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Tuple

import yaml

from .. import db
from ..settings import CONFIG_DIR
from ..util import dumps, loads

TOOLS_FILE = CONFIG_DIR / "tools.yaml"


def load_catalog() -> List[Dict[str, Any]]:
    with open(TOOLS_FILE, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return data.get("tools", [])


def sync_to_db() -> int:
    """把 yaml 中的工具定义同步进数据库（工具白名单的唯一来源）。"""
    catalog = load_catalog()
    for tool in catalog:
        db.execute(
            """INSERT INTO tools (name, title, description, params, category, risk_hint, command_template)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 title = excluded.title,
                 description = excluded.description,
                 params = excluded.params,
                 category = excluded.category,
                 risk_hint = excluded.risk_hint,
                 command_template = excluded.command_template""",
            (
                tool["name"],
                tool.get("title", tool["name"]),
                tool.get("description", ""),
                dumps(tool.get("params", {})),
                tool.get("category", "read"),
                tool.get("risk_hint", "LOW"),
                tool.get("command_template", ""),
            ),
        )
    return len(catalog)


def get_tool(name: str) -> Dict[str, Any]:
    row = db.query_one("SELECT * FROM tools WHERE name = ?", (name,))
    if row is None:
        return {}
    row["params"] = loads(row.get("params"), {}) or {}
    return row


def list_tools() -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM tools ORDER BY category, name")
    for row in rows:
        row["params"] = loads(row.get("params"), {}) or {}
    return rows


class ToolArgumentError(ValueError):
    pass


def validate_arguments(tool: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """按 yaml 中的 params 定义做类型 + 正则白名单校验，返回规整后的参数。"""
    spec: Dict[str, Any] = tool.get("params") or {}
    arguments = arguments or {}
    unknown = [k for k in arguments if k not in spec]
    if unknown:
        raise ToolArgumentError("工具 %s 不支持参数：%s" % (tool["name"], ", ".join(unknown)))

    clean: Dict[str, Any] = {}
    for key, meta in spec.items():
        meta = meta or {}
        value = arguments.get(key, meta.get("default"))
        if value is None or value == "":
            if meta.get("required"):
                raise ToolArgumentError("缺少必填参数：%s" % key)
            continue
        if meta.get("type") == "integer":
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ToolArgumentError("参数 %s 必须是整数" % key)
            value = max(1, min(value, 200))
        else:
            value = str(value)
            pattern = meta.get("pattern")
            if pattern and not re.match(pattern, value):
                raise ToolArgumentError("参数 %s 含有非法字符，已按白名单规则拒绝" % key)
            value = value[:2000]
        clean[key] = value
    return clean


def build_command(tool: Dict[str, Any], arguments: Dict[str, Any]) -> str:
    template = tool.get("command_template") or ""
    derived = dict(arguments or {})
    # 需要写入文件内容的工具（如 modify_config）：内容以 base64 传入，避免引号/换行注入
    if "content" in derived:
        derived["content_b64"] = base64.b64encode(str(derived["content"]).encode("utf-8")).decode("ascii")
    try:
        return template.format(**derived)
    except (KeyError, IndexError):
        return template


def openai_tool_schema(tool: Dict[str, Any]) -> Dict[str, Any]:
    """转换成 OpenAI / 兼容接口的 tools 参数格式。"""
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for key, meta in (tool.get("params") or {}).items():
        meta = meta or {}
        properties[key] = {
            "type": "integer" if meta.get("type") == "integer" else "string",
            "description": meta.get("description", ""),
        }
        if meta.get("required"):
            required.append(key)
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description") or tool.get("title", ""),
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def agent_tool_schemas(agent_id: int) -> Tuple[List[Dict[str, Any]], List[str]]:
    names = [
        r["tool_name"]
        for r in db.query("SELECT tool_name FROM agent_tools WHERE agent_id = ?", (agent_id,))
    ]
    schemas = []
    for name in names:
        tool = get_tool(name)
        if tool:
            schemas.append(openai_tool_schema(tool))
    return schemas, names
