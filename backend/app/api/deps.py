"""接口层公共依赖：当前登录用户。"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Header, HTTPException

from .. import db
from ..security import auth


def current_user(authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    payload = auth.verify_token(token.strip())
    if payload is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    user = db.query_one("SELECT * FROM users WHERE id = ?", (payload.get("sub"),))
    if user is None or not user["enabled"]:
        raise HTTPException(status_code=401, detail="账号不可用")
    user.pop("password_hash", None)
    user.pop("password_salt", None)
    return user


def require_role(user: Dict[str, Any], roles) -> None:
    if user["role"] not in roles:
        raise HTTPException(status_code=403, detail="当前角色无权执行该操作（需要：%s）" % "、".join(roles))

