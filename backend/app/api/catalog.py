"""资产、Agent、工具、策略的管理接口。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..ai import tools as tool_registry
from ..proxy import ssh_proxy
from ..security import policy as policy_engine
from ..security import secrets
from ..util import now_iso
from .deps import current_user, require_role

router = APIRouter(prefix="/api", tags=["资产与配置"])


# ------------------------------------------------------------------ 资产

@router.get("/assets")
def list_assets(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM assets ORDER BY CASE WHEN env_tag = 'production' THEN 0 ELSE 1 END, name"
    )
    granted = {
        r["asset_id"] for r in db.query("SELECT asset_id FROM user_assets WHERE user_id = ?", (user["id"],))
    }
    for row in rows:
        row["authorized"] = row["id"] in granted
        row["credential_hint"] = secrets.describe(row.get("credential_ref") or "")
        if row.get("auth_type") == "key":
            key_path = row.get("credential_ref") or ""
            row["credential_hint"] = "私钥 %s" % key_path if key_path else "未配置"
            row["has_credential"] = bool(key_path) and Path(key_path).expanduser().exists()
        else:
            row["has_credential"] = bool(row.get("credential_ref")) and secrets.get(row["credential_ref"]) is not None
        row.pop("credential_ref", None)
        row["agent_count"] = int((db.query_one(
            "SELECT COUNT(*) AS n FROM agent_assets WHERE asset_id = ?", (row["id"],)
        ) or {}).get("n") or 0)
        row["user_ids"] = [
            r["user_id"] for r in db.query("SELECT user_id FROM user_assets WHERE asset_id = ?", (row["id"],))
        ]
    return rows


class AssetCreate(BaseModel):
    name: str
    description: str = ""
    env_tag: str = "test"
    executor: str = "mock"
    host: str = ""
    port: int = 22
    ssh_user: str = "root"
    auth_type: str = "password"
    secret: str = ""              # 口令（仅管理员可见，写入本地凭据库，不入库）
    key_path: str = ""            # 私钥路径（auth_type=key 时使用）
    credential_ref: str = ""      # 高级用法：直接指定环境变量名
    user_ids: List[int] = []


@router.post("/assets")
def create_asset(payload: AssetCreate, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    if payload.executor not in ("mock", "ssh", "local"):
        raise HTTPException(status_code=400, detail="executor 只能是 mock / ssh / local")
    if payload.executor == "ssh" and not payload.host.strip():
        raise HTTPException(status_code=400, detail="纳管真实 Linux 资产必须填写主机地址")

    credential_ref = _resolve_credential(payload, asset_name=payload.name)
    asset_id = db.execute(
        """INSERT INTO assets (name, description, env_tag, executor, host, port, ssh_user,
                               auth_type, credential_ref, tags, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 'online', ?)""",
        (payload.name, payload.description, payload.env_tag, payload.executor, payload.host,
         payload.port, payload.ssh_user, payload.auth_type, credential_ref, now_iso()),
    )
    for user_id in payload.user_ids or [user["id"]]:
        db.execute("INSERT OR IGNORE INTO user_assets (user_id, asset_id) VALUES (?, ?)", (user_id, asset_id))
    from ..audit import logger as audit

    audit.record("asset_updated", actor=user["username"], payload={
        "action": "create", "name": payload.name, "executor": payload.executor,
        "host": payload.host, "env_tag": payload.env_tag,
    })
    return db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,)) or {}


class AssetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    env_tag: Optional[str] = None
    executor: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    ssh_user: Optional[str] = None
    auth_type: Optional[str] = None
    secret: Optional[str] = None
    key_path: Optional[str] = None
    status: Optional[str] = None
    user_ids: Optional[List[int]] = None


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, payload: AssetUpdate,
                 user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")

    fields: Dict[str, Any] = {}
    for key in ("name", "description", "env_tag", "executor", "host", "port",
                "ssh_user", "auth_type", "status"):
        value = getattr(payload, key)
        if value is not None:
            fields[key] = value
    if fields:
        db.execute(
            "UPDATE assets SET %s WHERE id = ?" % ", ".join("%s = ?" % k for k in fields),
            list(fields.values()) + [asset_id],
        )
    if payload.secret:
        ref = asset.get("credential_ref") or secrets.make_ref(payload.name or asset["name"])
        secrets.put(ref, payload.secret)
        db.execute("UPDATE assets SET credential_ref = ? WHERE id = ?", (ref, asset_id))
    elif payload.key_path:
        db.execute("UPDATE assets SET credential_ref = ?, auth_type = 'key' WHERE id = ?",
                   (payload.key_path, asset_id))
    if payload.user_ids is not None:
        db.execute("DELETE FROM user_assets WHERE asset_id = ?", (asset_id,))
        for user_id in payload.user_ids:
            db.execute("INSERT OR IGNORE INTO user_assets (user_id, asset_id) VALUES (?, ?)", (user_id, asset_id))

    from ..audit import logger as audit

    audit.record("asset_updated", actor=user["username"],
                 payload={"action": "update", "asset_id": asset_id, "fields": sorted(fields.keys())})
    return db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,)) or {}


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    if db.query_one("SELECT 1 FROM sessions WHERE asset_id = ? LIMIT 1", (asset_id,)):
        raise HTTPException(status_code=400, detail="该资产已存在历史会话，出于审计要求不可删除，可将其状态改为离线")
    db.execute("DELETE FROM user_assets WHERE asset_id = ?", (asset_id,))
    db.execute("DELETE FROM agent_assets WHERE asset_id = ?", (asset_id,))
    db.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    if asset.get("credential_ref"):
        secrets.drop(asset["credential_ref"])
    from ..audit import logger as audit

    audit.record("asset_updated", actor=user["username"],
                 payload={"action": "delete", "name": asset["name"]})
    return {"ok": True}


@router.post("/assets/{asset_id}/test")
def test_asset(asset_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    """连通性测试：ssh 执行器会真的登录目标机执行 uptime。"""
    asset = db.query_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    if user["role"] == "operator" and db.query_one(
        "SELECT 1 FROM user_assets WHERE user_id = ? AND asset_id = ?", (user["id"], asset_id)
    ) is None:
        raise HTTPException(status_code=403, detail="您没有该资产的访问权限")
    return ssh_proxy.test_connection(asset)


def _resolve_credential(payload: AssetCreate, asset_name: str) -> str:
    """把表单里的口令/私钥收敛成一个引用名，明文只落在本地凭据库或环境变量。"""
    if payload.auth_type == "key":
        return payload.key_path or payload.credential_ref
    if payload.secret:
        ref = payload.credential_ref or secrets.make_ref(asset_name)
        secrets.put(ref, payload.secret)
        return ref
    return payload.credential_ref


# ------------------------------------------------------------------ Agent

def _agent_detail(agent: Dict[str, Any]) -> Dict[str, Any]:
    agent["assets"] = [
        dict(r) for r in db.query(
            """SELECT a.id, a.name, a.env_tag, a.executor, a.status FROM agent_assets aa
               JOIN assets a ON a.id = aa.asset_id WHERE aa.agent_id = ? ORDER BY a.name""",
            (agent["id"],),
        )
    ]
    agent["tools"] = [
        r["tool_name"] for r in
        db.query("SELECT tool_name FROM agent_tools WHERE agent_id = ? ORDER BY tool_name", (agent["id"],))
    ]
    agent["users"] = [
        dict(r) for r in db.query(
            """SELECT u.id, u.username, u.display_name, u.role FROM user_agents ua
               JOIN users u ON u.id = ua.user_id WHERE ua.agent_id = ?""",
            (agent["id"],),
        )
    ]
    agent["session_count"] = int(
        (db.query_one("SELECT COUNT(*) AS n FROM sessions WHERE agent_id = ?", (agent["id"],)) or {}).get("n") or 0
    )
    return agent


@router.get("/agents")
def list_agents(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM agents ORDER BY name")
    allowed = {
        r["agent_id"] for r in db.query("SELECT agent_id FROM user_agents WHERE user_id = ?", (user["id"],))
    }
    for row in rows:
        row["authorized"] = row["id"] in allowed
        _agent_detail(row)
    return rows


class AgentCreate(BaseModel):
    name: str
    description: str = ""
    model: str = "rule-based-planner"
    system_prompt: str = ""
    status: str = "enabled"
    asset_ids: List[int] = []
    tools: List[str] = []
    user_ids: List[int] = []


@router.post("/agents")
def create_agent(payload: AgentCreate, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    agent_id = db.execute(
        """INSERT INTO agents (name, description, model, system_prompt, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (payload.name, payload.description, payload.model, payload.system_prompt, payload.status, now_iso()),
    )
    for asset_id in payload.asset_ids:
        db.execute("INSERT OR IGNORE INTO agent_assets (agent_id, asset_id) VALUES (?, ?)", (agent_id, asset_id))
    for tool_name in payload.tools:
        db.execute("INSERT OR IGNORE INTO agent_tools (agent_id, tool_name) VALUES (?, ?)", (agent_id, tool_name))
    for user_id in payload.user_ids:
        db.execute("INSERT OR IGNORE INTO user_agents (user_id, agent_id) VALUES (?, ?)", (user_id, agent_id))
    from ..audit import logger as audit

    audit.record("asset_updated", actor=user["username"], payload={"action": "create_agent", "name": payload.name})
    return _agent_detail(db.query_one("SELECT * FROM agents WHERE id = ?", (agent_id,)) or {})


class AgentGrant(BaseModel):
    asset_ids: Optional[List[int]] = None
    tools: Optional[List[str]] = None
    user_ids: Optional[List[int]] = None
    status: Optional[str] = None


@router.put("/agents/{agent_id}")
def update_agent(agent_id: int, payload: AgentGrant,
                 user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    if db.query_one("SELECT 1 FROM agents WHERE id = ?", (agent_id,)) is None:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    if payload.status:
        db.execute("UPDATE agents SET status = ? WHERE id = ?", (payload.status, agent_id))
    if payload.asset_ids is not None:
        db.execute("DELETE FROM agent_assets WHERE agent_id = ?", (agent_id,))
        for asset_id in payload.asset_ids:
            db.execute("INSERT OR IGNORE INTO agent_assets (agent_id, asset_id) VALUES (?, ?)", (agent_id, asset_id))
    if payload.tools is not None:
        db.execute("DELETE FROM agent_tools WHERE agent_id = ?", (agent_id,))
        for tool_name in payload.tools:
            db.execute("INSERT OR IGNORE INTO agent_tools (agent_id, tool_name) VALUES (?, ?)", (agent_id, tool_name))
    if payload.user_ids is not None:
        db.execute("DELETE FROM user_agents WHERE agent_id = ?", (agent_id,))
        for user_id in payload.user_ids:
            db.execute("INSERT OR IGNORE INTO user_agents (user_id, agent_id) VALUES (?, ?)", (user_id, agent_id))
    from ..audit import logger as audit

    audit.record("asset_updated", actor=user["username"], payload={"action": "update_agent", "agent_id": agent_id})
    return _agent_detail(db.query_one("SELECT * FROM agents WHERE id = ?", (agent_id,)) or {})


# ------------------------------------------------------------------ 工具

@router.get("/tools")
def list_tools(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return tool_registry.list_tools()


# ------------------------------------------------------------------ 策略

@router.get("/policies")
def list_policies(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return policy_engine.list_policies()


class PolicyRule(BaseModel):
    tool: str
    action: str
    reason: str = ""


class PolicyCreate(BaseModel):
    name: str
    description: str = ""
    scope_type: str = "env"
    scope_value: str = "*"
    priority: int = 100
    enabled: bool = True
    rules: List[PolicyRule] = []


@router.post("/policies")
def create_policy(payload: PolicyCreate, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    policy_id = policy_engine.create_policy(payload.model_dump())
    from ..audit import logger as audit

    audit.record("asset_updated", actor=user["username"], payload={"action": "create_policy", "name": payload.name})
    return {"id": policy_id}


@router.post("/policies/{policy_id}/rules")
def add_rule(policy_id: int, payload: PolicyRule,
             user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    rule_id = policy_engine.add_rule(policy_id, payload.model_dump())
    return {"id": rule_id}


@router.delete("/policies/rules/{rule_id}")
def delete_rule(rule_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    policy_engine.delete_rule(rule_id)
    return {"ok": True}


@router.delete("/policies/{policy_id}")
def delete_policy(policy_id: int, user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    require_role(user, ["security_admin"])
    db.execute("DELETE FROM policies WHERE id = ?", (policy_id,))
    return {"ok": True}


# ------------------------------------------------------------------ 用户

@router.get("/users")
def list_users(user: Dict[str, Any] = Depends(current_user)) -> List[Dict[str, Any]]:
    return [
        {"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"]}
        for r in db.query("SELECT * FROM users ORDER BY id")
    ]
