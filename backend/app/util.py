"""通用小工具函数。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).replace(microsecond=0).isoformat()


def now_dt() -> datetime:
    return datetime.now(CN_TZ)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(raw: Any, default: Any = None):
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return default


def session_code(seq: int) -> str:
    return "AI-%s-%04d" % (now_dt().strftime("%Y%m%d"), seq)

