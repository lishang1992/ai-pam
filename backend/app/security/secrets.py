"""本地凭据保管库。

约定：**数据库里永远不存口令**。资产表只保存一个引用名（credential_ref），
真实口令存在 data/secrets.json（文件权限 600），取值顺序：
    1) 环境变量（推荐生产使用，例如 AIB_SSH_PASSWORD_PROD01）
    2) 本地凭据文件（方便在控制台里直接添加资产）
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional

from ..settings import DATA_DIR

SECRETS_FILE = Path(DATA_DIR) / "secrets.json"


def _load() -> Dict[str, str]:
    if not SECRETS_FILE.exists():
        return {}
    try:
        return json.loads(SECRETS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: Dict[str, str]) -> None:
    SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(str(SECRETS_FILE), 0o600)
    except OSError:
        pass


def make_ref(asset_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", asset_name).strip("_").upper() or "ASSET"
    return "AIB_SSH_PASSWORD_%s" % slug


def put(ref: str, secret: str) -> None:
    data = _load()
    data[ref] = secret
    _save(data)


def drop(ref: str) -> None:
    data = _load()
    if ref in data:
        data.pop(ref)
        _save(data)


def get(ref: str) -> Optional[str]:
    """先查环境变量，再查本地凭据文件。"""
    if not ref:
        return None
    from_env = os.getenv(ref)
    if from_env:
        return from_env
    return _load().get(ref)


def describe(ref: str) -> str:
    if not ref:
        return "未配置"
    if os.getenv(ref):
        return "环境变量 %s" % ref
    if ref in _load():
        return "本地凭据库"
    return "未配置（%s）" % ref

