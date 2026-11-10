"""SQLite 访问层。

第一版刻意不引入 ORM：用标准库 sqlite3 + 少量辅助函数，
让 Windows 上「解压即用、无编译依赖」，也方便现场直接看库。
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional

from .settings import DB_PATH

_lock = threading.RLock()
_local = threading.local()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """事务上下文。同一个进程内串行化写操作，避免 SQLite 写冲突。"""
    with _lock:
        conn = get_conn()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with _lock:
        cur = get_conn().execute(sql, tuple(params))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, tuple(params))
        lastrowid = cur.lastrowid
        cur.close()
        return lastrowid


def execute_many(sql: str, seq: Iterable[Iterable[Any]]) -> None:
    with tx() as conn:
        conn.executemany(sql, [tuple(p) for p in seq])


def init_db(schema_file) -> None:
    """按 schema.sql 建表（幂等）。"""
    sql = open(schema_file, "r", encoding="utf-8").read()
    with tx() as conn:
        conn.executescript(sql)

