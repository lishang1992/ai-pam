"""登录 / 当前用户。"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..audit import logger as audit
from ..security import auth
from .deps import current_user

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(payload: LoginRequest) -> Dict[str, Any]:
    user = db.query_one("SELECT * FROM users WHERE username = ?", (payload.username.strip(),))
    if user is None or not user["enabled"]:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not auth.verify_password(payload.password, user["password_salt"], user["password_hash"]):
        audit.record("login_failed", actor=payload.username, payload={"reason": "口令校验失败"})
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = auth.issue_token({"sub": user["id"], "username": user["username"], "role": user["role"]})
    audit.record("login_success", actor=user["username"], payload={"role": user["role"]})
    return {
        "token": token,
        "user": {
            "id": user["id"], "username": user["username"], "display_name": user["display_name"],
            "role": user["role"],
        },
    }


@router.get("/me")
def me(user: Dict[str, Any] = Depends(current_user)) -> Dict[str, Any]:
    return {
        "id": user["id"], "username": user["username"],
        "display_name": user["display_name"], "role": user["role"],
    }

