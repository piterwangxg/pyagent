"""SQLite 存储层：模型配置持久化。

只依赖标准库 sqlite3，数据库文件放在 wy-agent-server/data/app.db。
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(__file__).resolve().parent / "data" / "app.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    provider     TEXT    NOT NULL DEFAULT 'openai',
    base_url     TEXT    NOT NULL DEFAULT '',
    api_key      TEXT    NOT NULL DEFAULT '',
    model        TEXT    NOT NULL DEFAULT '',
    temperature  REAL    NOT NULL DEFAULT 0.7,
    max_tokens   INTEGER NOT NULL DEFAULT 2048,
    is_active    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def get_conn():
    """每次请求一个连接，退出时提交并关闭。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def list_models() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM models ORDER BY is_active DESC, id DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_model(model_id: int) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    return _row_to_dict(row) if row else None


def create_model(data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO models
                (name, provider, base_url, api_key, model, temperature, max_tokens,
                 is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                data["name"],
                data["provider"],
                data["base_url"],
                data["api_key"],
                data["model"],
                data["temperature"],
                data["max_tokens"],
                now,
                now,
            ),
        )
        model_id = cur.lastrowid
    return get_model(model_id)  # type: ignore[return-value]


def update_model(model_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
    # api_key 为空字符串表示"保持原值不变"，避免编辑时把密钥覆盖掉
    fields = ["name", "provider", "base_url", "model", "temperature", "max_tokens"]
    sets = [f"{f} = ?" for f in fields]
    values: list[Any] = [data[f] for f in fields]

    if data.get("api_key"):
        sets.append("api_key = ?")
        values.append(data["api_key"])

    sets.append("updated_at = ?")
    values.append(_now())
    values.append(model_id)

    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE models SET {', '.join(sets)} WHERE id = ?", values
        )
        if cur.rowcount == 0:
            return None
    return get_model(model_id)


def delete_model(model_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
    return cur.rowcount > 0


def activate_model(model_id: int) -> Optional[dict[str, Any]]:
    """同一时刻只允许一个模型处于启用状态。"""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM models WHERE id = ?", (model_id,)).fetchone()
        if not row:
            return None
        conn.execute("UPDATE models SET is_active = 0, updated_at = ?", (_now(),))
        conn.execute(
            "UPDATE models SET is_active = 1, updated_at = ? WHERE id = ?",
            (_now(), model_id),
        )
    return get_model(model_id)


def get_active_model() -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM models WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _row_to_dict(row) if row else None
