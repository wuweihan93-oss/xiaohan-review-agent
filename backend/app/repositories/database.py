"""PostgreSQL 连接池与兼容旧版本的表初始化；只在应用 lifespan 中执行。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import (
    DB_CONNECT_TIMEOUT_SECONDS,
    DB_POOL_MAX_SIZE,
    DB_POOL_TIMEOUT_SECONDS,
    DB_URL,
)

_pool: AsyncConnectionPool | None = None


@asynccontextmanager
async def history_context() -> AsyncIterator[None]:
    global _pool
    if not DB_URL:
        yield
        return
    async with AsyncConnectionPool(
        DB_URL,
        min_size=1,
        max_size=DB_POOL_MAX_SIZE,
        open=False,
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "connect_timeout": DB_CONNECT_TIMEOUT_SECONDS,
        },
    ) as pool:
        await pool.wait(timeout=DB_POOL_TIMEOUT_SECONDS)
        _pool = pool
        try:
            async with pool.connection() as connection:
                async with connection.transaction():
                    await connection.execute("""
                        CREATE TABLE IF NOT EXISTS xiaohan_conversations (
                            id TEXT PRIMARY KEY,
                            title TEXT NOT NULL,
                            material_ids JSONB NOT NULL DEFAULT '[]',
                            study_mode TEXT NOT NULL DEFAULT 'coach',
                            prompt TEXT,
                            web_search_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    # 已部署的旧表按需补列，旧会话默认不启用联网。
                    await connection.execute("""
                        ALTER TABLE xiaohan_conversations
                        ADD COLUMN IF NOT EXISTS web_search_enabled BOOLEAN NOT NULL DEFAULT FALSE
                    """)
                    # 旧记录保留空归属，不向任何匿名访客公开，也不允许自动认领。
                    await connection.execute("""
                        ALTER TABLE xiaohan_conversations ADD COLUMN IF NOT EXISTS visitor_id TEXT
                    """)
                    await connection.execute("""
                        CREATE INDEX IF NOT EXISTS xiaohan_conversations_visitor_idx
                        ON xiaohan_conversations(visitor_id, updated_at DESC, id)
                    """)
                    await connection.execute("""
                        CREATE TABLE IF NOT EXISTS xiaohan_messages (
                            position BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            id TEXT NOT NULL UNIQUE,
                            conversation_id TEXT NOT NULL REFERENCES xiaohan_conversations(id) ON DELETE CASCADE,
                            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                            content TEXT NOT NULL,
                            sources JSONB NOT NULL DEFAULT '[]',
                            trace JSONB NOT NULL DEFAULT '[]',
                            failed BOOLEAN NOT NULL DEFAULT FALSE,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    await connection.execute("""
                        CREATE INDEX IF NOT EXISTS xiaohan_messages_conversation_idx
                        ON xiaohan_messages(conversation_id, position)
                    """)
                    # 升级前未记录的来源地址保留为空，不用当前请求的地址补写历史。
                    await connection.execute("""
                        ALTER TABLE xiaohan_messages ADD COLUMN IF NOT EXISTS client_ip TEXT
                    """)
                    await connection.execute("""
                        CREATE TABLE IF NOT EXISTS xiaohan_admin_sessions (
                            token_hash TEXT PRIMARY KEY,
                            csrf_token TEXT NOT NULL,
                            credential_version TEXT NOT NULL,
                            expires_at TIMESTAMPTZ NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                        )
                    """)
                    await connection.execute("""
                        CREATE INDEX IF NOT EXISTS xiaohan_admin_sessions_expiry_idx
                        ON xiaohan_admin_sessions(expires_at)
                    """)
            yield
        finally:
            _pool = None


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("历史复习服务不可用，请检查项目 .env 中的 DB_URL")
    return _pool
