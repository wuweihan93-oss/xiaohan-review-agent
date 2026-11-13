"""SQLite 只登记资料元信息，切片向量由 Milvus 保存。"""

import sqlite3
from typing import Any

from app.core.config import REGISTRY_PATH


def open_registry() -> sqlite3.Connection:
    connection = sqlite3.connect(REGISTRY_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute(
        """CREATE TABLE IF NOT EXISTS materials (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            source TEXT NOT NULL UNIQUE,
            origin TEXT NOT NULL,
            stored_path TEXT,
            content_hash TEXT NOT NULL,
            chunk_count INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    connection.commit()
    return connection


def list_materials() -> list[dict[str, Any]]:
    registry = open_registry()
    try:
        rows = registry.execute(
            "SELECT id, title, source, origin, chunk_count, created_at FROM materials ORDER BY origin, title COLLATE NOCASE"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        registry.close()


def material_counts() -> dict[str, int]:
    registry = open_registry()
    try:
        rows = registry.execute(
            "SELECT origin, COUNT(*) AS count FROM materials GROUP BY origin"
        ).fetchall()
        result = {"tutorial": 0, "upload": 0}
        for row in rows:
            if row["origin"] in result:
                result[row["origin"]] = int(row["count"])
        return result
    finally:
        registry.close()


def find_by_source(connection: sqlite3.Connection, source: str):
    return connection.execute(
        "SELECT id FROM materials WHERE source = ?", (source,)
    ).fetchone()


def find_by_id(connection: sqlite3.Connection, material_id: str):
    return connection.execute(
        "SELECT * FROM materials WHERE id = ?", (material_id,)
    ).fetchone()


def save_material(connection: sqlite3.Connection, values: tuple) -> None:
    """写登记信息，事务由业务层在向量写入成功后提交。"""
    connection.execute(
        """INSERT INTO materials (id, title, source, origin, stored_path, content_hash, chunk_count, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source) DO UPDATE SET
             id=excluded.id, title=excluded.title, origin=excluded.origin,
             stored_path=excluded.stored_path, content_hash=excluded.content_hash,
             chunk_count=excluded.chunk_count, created_at=excluded.created_at""",
        values,
    )


def remove_material(connection: sqlite3.Connection, material_id: str) -> None:
    connection.execute("DELETE FROM materials WHERE id = ?", (material_id,))
